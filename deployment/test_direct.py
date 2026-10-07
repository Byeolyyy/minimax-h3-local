"""Regression checks for the prompt-to-H3 path; no model loading required."""
import atexit
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

# Importing the server restores persisted jobs. Keep that startup side effect
# away from the user's history, including any generation currently running.
test_root = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
atexit.register(test_root.cleanup)
(Path(test_root.name) / 'deployment').mkdir()
with patch.dict('os.environ', {'H3_ROOT': test_root.name}):
    import studio_server as server


class DirectGenerationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=server.ROOT / 'deployment')
        self.patches = [patch.object(server, 'DATA', Path(self.temp.name)),
                        patch.object(server, 'JOBS', {}), patch.object(server, 'ACTIVE', None),
                        patch.object(server, 'PROCESS', None), patch.object(server, 'WORKER', None),
                        patch.object(server, 'advanced_process', return_value=None)]
        for item in self.patches:
            item.start()
        server.CANCEL.clear()
        self.client = TestClient(server.app)

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        server.CANCEL.clear()
        self.temp.cleanup()

    def test_multiline_prompt_is_one_unchanged_request(self):
        prompt = '  清晨竹林，一只小熊猫转头。\n\n镜头慢慢推进。\n'
        response = self.client.post('/api/plan', json={'prompt': prompt, 'seed': 0})
        self.assertEqual(response.status_code, 200)
        settings = response.json()['settings']
        self.assertEqual(settings['prompt'], prompt)
        self.assertEqual(settings['seed'], 0)
        self.assertEqual(settings['multi_prompts_gen_type'], 'FG')
        self.assertEqual(settings['prompt_enhancer'], '')
        self.assertEqual(settings['activated_loras'], [])

    def test_invalid_or_old_storyboard_requests_are_rejected(self):
        cases = [{'prompt': '  \n'}, {'prompt': 'panda', 'layout': 'story'},
                 {'prompt': 'panda', 'shots': []}, {'prompt': 'panda', 'seconds': 0},
                 {'prompt': 'panda', 'seconds': 5, 'video_length': 124},
                 {'prompt': 'panda', 'video_length': 120}, {'prompt': 'panda', 'video_length': 500},
                 {'prompt': 'panda', 'resolution': 'invalid'}, {'prompt': 'panda', 'seed': -1}]
        for payload in cases:
            with self.subTest(payload=payload):
                self.assertEqual(self.client.post('/api/jobs', json=payload).status_code, 422)

    def test_custom_duration_uses_native_continuation(self):
        import runpy
        upstream = Path(os.environ.get('H3_WAN2GP_ROOT', Path(__file__).resolve().parents[1]))
        scheduler_path = upstream / 'shared/utils/frame_scheduler.py'
        if not scheduler_path.is_file():
            self.skipTest('Set H3_WAN2GP_ROOT to validate native Wan2GP window scheduling')
        scheduler = runpy.run_path(str(scheduler_path))
        for seconds in (4.5, 5, 16, 30, 60, 120, 300):
            with self.subTest(seconds=seconds):
                prompt = 'A red panda in a bamboo forest.\n\nSlow camera movement.'
                result = self.client.post('/api/plan', json={'prompt': prompt, 'seconds': seconds})
                self.assertEqual(result.status_code, 200)
                settings = result.json()['settings']
                frames = settings['video_length']
                self.assertEqual(frames, scheduler['normalize_output_frame_count'](round(seconds * 24), 107, 17, 5))
                self.assertLess(abs(frames / 24 - seconds), .36)
                self.assertEqual(settings['prompt'], prompt)
                self.assertEqual(settings['multi_prompts_gen_type'], 'FG')
                windows = scheduler['build_default_window_plan'](
                    total_frames=frames, window_size=362, default_overlap=18,
                    discard_last_frames=0, minimum=107, step=17, frame_offset=5,
                    overlap_offset=1, max_overlap=120, output_frame_policy='exact')
                self.assertEqual(sum(window['output_frames'] for window in windows), frames)
                if seconds > 16:
                    self.assertGreater(len(windows), 1)
                self.assertTrue(all(window['frame_num'] <= 362 for window in windows))

    def test_submit_runs_only_the_h3_cli(self):
        with patch.object(server, 'threading'), patch('httpx.get') as probe:
            probe.return_value.status_code = 404
            response = self.client.post('/api/jobs', json={'prompt': 'A red panda.\n\nMorning light.'})
            self.assertEqual(response.status_code, 200)
            job = response.json()
            folder = server.DATA / job['id']
            settings = json.loads((folder / 'settings.json').read_text(encoding='utf-8'))
            self.assertEqual(settings['prompt'], job['request']['prompt'])
            self.assertEqual(settings['seed'], job['seed'])
            self.assertEqual(self.client.post('/api/jobs', json={'prompt': 'second'}).status_code, 409)
        def launch(command, **kwargs):
            self.assertIn('launch_h3.py', command[2])
            self.assertIn('--process', command)
            self.assertNotIn('story_planner', ' '.join(command))
            (folder / 'output' / 'test.mp4').write_bytes(b'fake-test-video')
            from unittest.mock import Mock
            process = Mock()
            process.poll.return_value = 0
            process.wait.return_value = 0
            return process
        with patch.object(server.subprocess, 'Popen', side_effect=launch) as process:
            server.run_generation(job['id'])
            self.assertEqual(process.call_count, 1)
        self.assertEqual(server.JOBS[job['id']]['status'], 'completed')
        self.assertIsNone(server.ACTIVE)
        self.assertFalse((folder / 'planner-request.json').exists())
        self.assertEqual(self.client.get(f"/api/jobs/{job['id']}/media").status_code, 200)

    def test_removed_routes_and_foreign_origin(self):
        self.assertEqual(self.client.post('/api/advanced').status_code, 404)
        self.assertEqual(self.client.get('/api/jobs/' + 'a' * 24 + '/storyboard').status_code, 404)
        self.assertEqual(self.client.post('/api/jobs', json={'prompt': 'panda'},
                                         headers={'Origin': 'https://example.com'}).status_code, 403)
        self.assertEqual(self.client.get('/api/health').json()['app'], 'h3-direct')

    def fixture_job(self, job_id, saved=False, status='completed'):
        folder = server.DATA / job_id
        (folder / 'output').mkdir(parents=True)
        (folder / 'output/test.mp4').write_bytes(b'fixture-only-no-video-generation')
        job = {'id': job_id, 'status': status, 'created': time.time(), 'stage': status,
               'result': 'test.mp4', 'finished': time.time()}
        if saved is not None:
            job['saved'] = saved
        server.JOBS[job_id] = job
        server.save(job)
        return folder

    def test_preview_does_not_save_but_save_is_persistent(self):
        job_id = 'a' * 24
        folder = self.fixture_job(job_id)
        response = self.client.get(f'/api/jobs/{job_id}/media')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertFalse(server.JOBS[job_id]['saved'])
        self.assertEqual(self.client.get(f'/api/jobs/{job_id}/media?download=true').status_code, 409)
        self.assertTrue(self.client.post(f'/api/jobs/{job_id}/save').json()['saved'])
        self.assertTrue(json.loads((folder / 'job.json').read_text(encoding='utf-8'))['saved'])
        server.cleanup_temporary_jobs()
        self.assertTrue(folder.exists())
        self.assertEqual(self.client.get(f'/api/jobs/{job_id}/media?download=true').status_code, 200)

    def test_next_generation_discards_unsaved_only(self):
        unsaved = self.fixture_job('a' * 24)
        saved = self.fixture_job('b' * 24, saved=True)
        legacy = self.fixture_job('c' * 24, saved=None)
        with patch.object(server, 'threading'), patch('httpx.get') as probe:
            probe.return_value.status_code = 404
            response = self.client.post('/api/jobs', json={'prompt': 'fixture task, never run'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['saved'])
        self.assertFalse((unsaved / 'output').exists())
        self.assertEqual(server.JOBS['a' * 24]['status'], 'discarded')
        self.assertTrue((unsaved / 'job.json').exists())
        self.assertTrue(saved.exists())
        self.assertTrue(legacy.exists())

    def test_closed_pages_keep_completed_preview_available(self):
        folder = self.fixture_job('a' * 24)
        first, second = '1' * 32, '2' * 32
        with patch.object(server.time, 'monotonic', return_value=100):
            self.client.get('/api/state?client_id=' + first)
            self.client.get('/api/state?client_id=' + second)
            self.client.post('/api/session/close', json={'client_id': first})
        with patch.object(server.time, 'monotonic', return_value=116):
            server.cleanup_abandoned_previews()
            self.assertTrue(folder.exists())
            self.client.post('/api/session/close', json={'client_id': second})
        with patch.object(server.time, 'monotonic', return_value=120):
            server.cleanup_abandoned_previews()
            self.assertTrue(folder.exists())
        with patch.object(server.time, 'monotonic', return_value=132):
            server.cleanup_abandoned_previews()
            self.assertTrue((folder / 'output/test.mp4').exists())

    def test_lost_connection_never_cancels_or_cleans_active_files(self):
        folder = self.fixture_job('a' * 24, status='running')
        server.ACTIVE = 'a' * 24
        self.client.post('/api/session/close', json={'client_id': '1' * 32})
        with patch.object(server.time, 'time', return_value=time.time() + 3 * 86400):
            server.cleanup_abandoned_previews()
        self.assertFalse(server.CANCEL.is_set())
        self.assertTrue((folder / 'output/test.mp4').exists())
        server.ACTIVE = None
        server.JOBS['a' * 24]['status'] = 'cancelled'
        server.cleanup_temporary_jobs()
        self.assertFalse((folder / 'output').exists())
        self.assertEqual(server.JOBS['a' * 24]['status'], 'cancelled')

    def test_crash_recovery_only_removes_explicitly_unsaved_jobs(self):
        unsaved = self.fixture_job('a' * 24, status='running')
        saved = self.fixture_job('b' * 24, saved=True)
        legacy = self.fixture_job('c' * 24, saved=None)
        removed = server.cleanup_unsaved_on_disk(server.DATA)
        self.assertEqual(removed, ['a' * 24])
        self.assertFalse((unsaved / 'output').exists())
        restored = json.loads((unsaved / 'job.json').read_text(encoding='utf-8'))
        self.assertEqual(restored['status'], 'interrupted')
        self.assertIn('没有完成', restored['error'])
        self.assertTrue(saved.exists())
        self.assertTrue(legacy.exists())
        with self.assertRaises(ValueError):
            server.remove_job_directory(server.DATA, '../outside')

    def test_server_shutdown_discards_temporary_files(self):
        with TestClient(server.app):
            unsaved = self.fixture_job('a' * 24)
            saved = self.fixture_job('b' * 24, saved=True)
        self.assertFalse((unsaved / 'output').exists())
        self.assertTrue((unsaved / 'job.json').exists())
        self.assertTrue(saved.exists())

    def test_expiry_starts_at_completion_and_keeps_diagnostics(self):
        folder = self.fixture_job('a' * 24)
        finished = server.JOBS['a' * 24]['finished']
        server.JOBS['a' * 24]['created'] = finished - 2 * 86400
        (folder / 'run.log').write_bytes(b'x' * 100000 + b'completion log')
        with patch.object(server.time, 'time', return_value=finished + 3 * 3600):
            server.cleanup_abandoned_previews()
        self.assertTrue((folder / 'output/test.mp4').exists())
        with patch.object(server.time, 'time', return_value=finished + 86401):
            server.cleanup_abandoned_previews()
        self.assertFalse((folder / 'output').exists())
        self.assertEqual((folder / 'run.log').stat().st_size, 65536)
        self.assertEqual(server.JOBS['a' * 24]['status'], 'discarded')
        self.assertIn('24 小时', server.JOBS['a' * 24]['cleanup_reason'])
        self.assertIn('completion log', self.client.get('/api/jobs/' + 'a' * 24 + '/log').json()['text'])

    def test_failed_job_keeps_error_and_log_after_cleanup_and_restart(self):
        folder = self.fixture_job('a' * 24, status='failed')
        server.JOBS['a' * 24]['error'] = 'fixture CUDA allocation error'
        (folder / 'run.log').write_text('RuntimeError: fixture CUDA allocation error', encoding='utf-8')
        server.cleanup_temporary_jobs()
        self.assertFalse((folder / 'output').exists())
        server.JOBS.clear()
        server.restore_jobs()
        self.assertEqual(server.JOBS['a' * 24]['status'], 'failed')
        self.assertIn('CUDA', server.JOBS['a' * 24]['error'])
        self.assertIn('CUDA', self.client.get('/api/jobs/' + 'a' * 24 + '/log').json()['text'])
        self.assertEqual(self.client.get('/api/jobs/' + 'b' * 24 + '/log').status_code, 410)

    def test_diagnostic_retention_is_bounded(self):
        for number in range(32):
            self.fixture_job(f'{number:024x}', status='failed')
        server.cleanup_temporary_jobs()
        self.assertEqual(len(server.JOBS), 30)
        self.assertEqual(len(list(server.DATA.iterdir())), 30)

    def test_long_prompt_is_written_verbatim_and_mismatch_never_launches(self):
        prompt = ('雨中的城市街道，一位成年人撑伞走过街角，镜头缓慢跟随。\n\n' * 150).strip()
        with patch.object(server, 'threading'), patch('httpx.get') as probe:
            probe.return_value.status_code = 404
            response = self.client.post('/api/jobs', json={'prompt': prompt, 'seconds': 120})
        self.assertEqual(response.status_code, 200)
        job = response.json()
        path = server.DATA / job['id'] / 'settings.json'
        settings = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(settings['prompt'], prompt)
        self.assertEqual(settings['video_length'], 2878)
        settings['prompt'] = 'An unrelated default example'
        path.write_text(json.dumps(settings), encoding='utf-8')
        with patch.object(server.subprocess, 'Popen') as process:
            server.run_generation(job['id'])
            process.assert_not_called()
        self.assertEqual(server.JOBS[job['id']]['status'], 'failed')
        self.assertIn('不一致', server.JOBS[job['id']]['error'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
