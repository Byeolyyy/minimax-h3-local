from pathlib import Path
import argparse
import shutil

old = '''def _strip_text_encoder_wrapper(state_dict, quantization_map=None, tied_weights_map=None):
    root = "language_model" if "model.embed_tokens.weight" in state_dict else ""
    return tuple(offload.map_state_dict([state_dict, quantization_map, tied_weights_map], rules={"model": root}))'''
new = '''def _strip_text_encoder_wrapper(state_dict, quantization_map=None, tied_weights_map=None, dtype=torch.bfloat16):
    # Local compatibility: Heretic uses a scalar INT8 scale for its embedding.
    # This is an Embedding, not a ConvRot Linear. Decode only this tensor;
    # all transformer linear layers retain their original NVFP4 representation.
    root = "language_model" if "model.embed_tokens.weight" in state_dict else ""
    state_dict, quantization_map, tied_weights_map = offload.map_state_dict(
        [state_dict, quantization_map, tied_weights_map], rules={"model": root})
    base = "language_model.embed_tokens"
    weight = state_dict.get(base + ".weight")
    scale = state_dict.get(base + ".weight_scale")
    config = state_dict.get(base + ".comfy_quant")
    if (weight is not None and weight.dtype == torch.int8 and scale is not None
            and scale.numel() == 1 and config is not None):
        import json
        quant_config = json.loads(bytes(config.tolist()).decode("utf-8"))
        if quant_config == {"format": "int8_tensorwise"}:
            decoded = torch.empty(weight.shape, dtype=dtype, device=weight.device)
            for start in range(0, weight.shape[0], 4096):
                decoded[start:start + 4096] = weight[start:start + 4096].float().mul_(scale.float()).to(dtype)
            state_dict[base + ".weight"] = decoded
            del state_dict[base + ".weight_scale"]
            del state_dict[base + ".comfy_quant"]
            print("H3 Heretic: decoded scalar INT8 embedding; NVFP4 layers preserved.")
    return state_dict, quantization_map, tied_weights_map'''
OLD_CALL = 'preprocess_sd=_strip_text_encoder_wrapper, ignore_unused_weights=True)'
NEW_CALL = 'preprocess_sd=partial(_strip_text_encoder_wrapper, dtype=dtype), ignore_unused_weights=True)'


def patched_source(source):
    if new in source and NEW_CALL in source:
        return source
    if old not in source or OLD_CALL not in source:
        raise ValueError('Unsupported upstream encoder source; refusing to patch')
    result = source.replace(old, new, 1).replace(OLD_CALL, NEW_CALL, 1)
    compile(result, 'minimax_h3_main.py', 'exec')
    return result


def apply(root):
    target = root / 'models/minimax_h3/minimax_h3_main.py'
    source = target.read_text(encoding='utf-8')
    updated = patched_source(source)
    if source == updated:
        print('Encoder compatibility patch already present.')
        return
    backup = root / 'deployment/minimax_h3_main.py.upstream'
    backup.parent.mkdir(parents=True, exist_ok=True)
    if not backup.exists():
        shutil.copy2(target, backup)
    target.write_text(updated, encoding='utf-8')
    print('Applied scalar INT8 embedding compatibility patch.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--wan2gp', type=Path, default=Path(__file__).resolve().parents[1])
    apply(parser.parse_args().wan2gp.resolve())
