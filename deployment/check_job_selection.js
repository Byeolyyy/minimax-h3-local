// Browser regression: simulate network responses only; never submit a real job.
(async () => {
  const check=(condition,message)=>{if(!condition)throw Error(message);};
  while(polling)await new Promise(resolve=>setTimeout(resolve,10));
  const previous={api,state,selected,currentJobId,viewingHistory,submissionError,
    prompt:$('prompt').value,seconds:$('seconds').value};
  const old={id:'1'.repeat(24),created:1,status:'completed',stage:'生成完成',elapsed:126,
    saved:true,result:'fixture.mp4',request:{prompt:'Old unrelated example'},
    plan:{seconds:4.458,resolution:'640x384',steps:12}};
  const prompt=('雨中街道，一位成年人撑伞走过街角，镜头缓慢跟随。\n\n').repeat(150);
  const fresh={id:'2'.repeat(24),created:2,status:'running',stage:'准备生成',elapsed:0,
    saved:false,request:{prompt,seconds:120},plan:{seconds:119.917,resolution:'832x480',steps:20}};
  let snapshot={active:null,jobs:[old]},held=false,releasePoll,captured,failSubmit=false;
  try{
    stateVersion++;state=snapshot;selected=null;rememberJob(null);viewingHistory=false;submissionError='';
    api=async(path,method,body)=>{
      if(path.startsWith('/api/state')){
        if(held){held=false;return new Promise(resolve=>{releasePoll=resolve;});}
        return structuredClone(snapshot);
      }
      if(path==='/api/jobs'&&method==='POST'){
        captured=body;
        if(failSubmit)throw Error('fixture: submission rejected');
        return structuredClone(fresh);
      }
      if(path.endsWith('/log'))return {text:'fixture log'};
      throw Error('Unexpected API call in regression test: '+path);
    };
    await poll();
    check(selected===null&&!$('preview').querySelector('video'),'Initial load must not select historical output');
    selected=old.id;viewingHistory=true;render();
    check($('status').textContent.includes('历史任务'),'Explicit history must be labelled');
    held=true;
    const stalePoll=poll();
    $('prompt').value=prompt;$('seconds').value='120';
    $('form').requestSubmit(); // Exercise the actual form handler and validity checks.
    while(submitting)await new Promise(resolve=>setTimeout(resolve,5));
    check(captured?.prompt===prompt&&captured.seconds===120,'Long prompt and custom seconds must be sent verbatim');
    releasePoll({active:null,jobs:[old]});await stalePoll;
    check(selected===fresh.id&&state.active===fresh.id,'Stale poll replaced the newly acknowledged job');
    check(sessionStorage.getItem(currentJobKey)===fresh.id,'Current job must survive refresh');
    check(!$('preview').querySelector('video'),'Old video remained visible during new generation');
    snapshot={active:null,jobs:[{...fresh,status:'failed',stage:'生成失败',error:'fixture failure'},old]};
    await poll();
    check($('status').textContent.includes('fixture failure')&&!$('preview').querySelector('video'),'Failure must not show old success');
    snapshot={active:null,jobs:[{...fresh,status:'completed',stage:'生成完成',elapsed:999,result:'fresh.mp4'},old]};
    await poll();
    check($('preview').querySelector('video')?.src.includes(fresh.id),'Completed preview must use the current job ID');
    check($('submitted-prompt').textContent===prompt,'Displayed submission must match the long prompt');
    check($('result-info').textContent.includes('119.917'),'Displayed duration must belong to current job');
    snapshot={active:null,jobs:[old]};await poll();
    check(selected===fresh.id&&!$('preview').querySelector('video'),'Missing job fell back to historical output');
    check($('status').textContent.includes('已不在后台记录中'),'Missing job needs an explicit explanation');
    failSubmit=true;$('form').requestSubmit();
    while(submitting)await new Promise(resolve=>setTimeout(resolve,5));
    await poll();
    check($('status').textContent.includes('本次提交失败')&&!$('preview').querySelector('video'),'Rejected request must not show an old video');
    return 'PASS: history isolation, stale-poll race, long prompt submission, current result, failure, missing task';
  }finally{
    api=previous.api;state=previous.state;selected=previous.selected;rememberJob(previous.currentJobId);
    viewingHistory=previous.viewingHistory;submissionError=previous.submissionError;stateVersion++;
    $('prompt').value=previous.prompt;$('seconds').value=previous.seconds;$('error').textContent='';render();
  }
})()
