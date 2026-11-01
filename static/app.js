const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="studio-token"]').content;
const activeStates = new Set(['uploading','queued','downloading','preparing','transcribing','translating','exporting','canceling','deleting']);
let jobs = [], selected = null, edits = [], dirty = false, uploading = false, lastState = '', polling = false;
let lastJobSignature = '';
let captionTrack = null, nativeCues = [];
let youtubeInspection = null, reuseJobId = null, inspectingYoutube = false;
let uploadAbort = null, uploadCancelled = false;
let removalWatch = null;
let anchor={x:50,y:90};
const media=()=> $('video').hidden ? $('audio') : $('video');
const english=()=> $('subtitle-language').value==='en';
function syncNativeCue(){updateCaption();}
function syncNativeCaptions(){updateCaption();}
const labels = {uploading:'Uploading video',queued:'In the queue',downloading:'Downloading video',preparing:'Preparing video',transcribing:'Recognizing speech',translating:'Translating to Persian',review:'Ready to review',exporting:'Exporting video',complete:'Export complete',error:'Needs attention',canceling:'Stopping…',cancelled:'Cancelled',deleting:'Deleting…'};
function message(id,text,error=false){ $(id).textContent=text; $(id).classList.toggle('error-text',error); }
async function api(path,body,method='POST'){
  const response=await fetch(path,{method,headers:{'Content-Type':'application/json','X-Studio-Token':token},body:body === undefined ? undefined : JSON.stringify(body)});
  if(!response.ok){const data=await response.json().catch(()=>({}));throw new Error(response.status===404&&data.detail==='Not Found'?'This studio is running an older version. Close its launcher window, open Start Studio.cmd again, and refresh this page.':data.detail || 'This request could not be completed.');}
  return response.json();
}
function asset(job,kind){return `/api/jobs/${job.id}/${kind}?token=${encodeURIComponent(token)}`;}
function setDirty(){dirty=true;$('save-state').textContent='Unsaved changes';$('export-note').textContent=$('video').hidden?'Save your edits before downloading the text or subtitles.':'The video download uses your last export. Export again to include your changes.';}
function releaseVideo(){
  for(const id of ['video','audio']){$(id).pause();$(id).removeAttribute('src');$(id).load();}
  if(captionTrack)while(captionTrack.cues.length)captionTrack.removeCue(captionTrack.cues[0]);
  nativeCues=[];selected=null;edits=[];dirty=false;lastState='';$('editor').hidden=true;
}
async function copyPath(path,note){
  note.textContent=path;
  try{await navigator.clipboard.writeText(path);note.textContent=`Copied folder path: ${path}`;}
  catch{note.textContent=`Copy this path into File Explorer: ${path}`;}
}
async function deleteVideo(job){
  if(!window.confirm(`Delete "${job.name}" from the studio? Its video files, subtitles and exports will move to the Recycle Bin. Running processing will stop.`))return;
  if(job.state==='uploading'){uploadCancelled=true;uploadAbort?.abort();}
  if(selected===job.id)releaseVideo();
  try{removalWatch=[job.id];await api(`/api/jobs/${job.id}/delete`);message('storage-status','Deleting the selected video…');await poll();}
  catch(error){removalWatch=null;message('storage-status',error.message,true);}
}
function renderJobs(){
  const signature=JSON.stringify([selected,jobs.map(job=>[job.id,job.name,job.state,job.progress,job.message,job.downloaded_resolution,job.folder_path])]);
  if(signature===lastJobSignature)return;
  lastJobSignature=signature;
  $('projects').hidden=!jobs.length;$('empty-state').hidden=!!jobs.length;$('project-count').textContent=`${jobs.length} video${jobs.length===1?'':'s'}`;
  $('clear-all').disabled=!jobs.length||jobs.every(job=>job.state==='deleting');
  const container=$('job-list');container.replaceChildren();
  jobs.forEach(job=>{
    const card=document.createElement('div');card.className='job-card'+(job.id===selected?' selected':'');
    card.tabIndex=0;card.setAttribute('role','button');card.setAttribute('aria-label',`${job.name}: ${labels[job.state]}`);
    const name=document.createElement('div');name.className='job-name';name.textContent=job.name;
    const state=document.createElement('div');state.className='job-state'+(job.state==='error'?' error':'');state.textContent=`${labels[job.state]}${activeStates.has(job.state)?` · ${job.progress}%`:''}`;
    card.append(name,state);
    if(job.folder_name){const folderLabel=document.createElement('div');folderLabel.className='job-message';folderLabel.textContent=`Folder: ${job.folder_name}`;card.append(folderLabel);}
    if(job.metadata){const resolution=document.createElement('div');resolution.className='job-message';resolution.textContent=job.metadata.width?`Source: ${job.metadata.width} × ${job.metadata.height}${job.quality_label?` · Selected ${job.quality_label}`:''}`:'Audio recording';card.append(resolution);}
    if(activeStates.has(job.state)){const bar=document.createElement('div');bar.className='progress';const fill=document.createElement('div');fill.style.width=`${job.progress}%`;bar.append(fill);card.append(bar);}
    const text=document.createElement('div');text.className='job-message';text.textContent=job.message;card.append(text);
    const note=document.createElement('div');note.className='job-action-note';note.setAttribute('role','status');
    const folder=document.createElement('button');folder.className='retry folder-button';folder.textContent='Open folder';folder.title=job.folder_path||'';folder.disabled=job.state==='deleting';folder.setAttribute('aria-label',`Open saved folder for ${job.name}`);folder.onclick=async event=>{event.stopPropagation();folder.disabled=true;note.textContent=`Folder: ${job.folder_path}`;try{await api(`/api/jobs/${job.id}/open-folder`);note.textContent=`Opened in File Explorer: ${job.folder_path}`;}catch(error){note.textContent=error.message;note.classList.add('error-text');}finally{folder.disabled=false;}};card.append(folder);
    const copy=document.createElement('button');copy.className='retry';copy.textContent='Copy path';copy.setAttribute('aria-label',`Copy folder path for ${job.name}`);copy.onclick=event=>{event.stopPropagation();copyPath(job.folder_path||'',note);};card.append(copy);
    const rename=document.createElement('button');rename.className='retry';rename.textContent='Rename folder';rename.disabled=activeStates.has(job.state);rename.setAttribute('aria-label',`Rename folder for ${job.name}`);rename.onclick=async event=>{event.stopPropagation();const name=window.prompt('Choose a folder name for this video:',job.folder_name||job.name.replace(/\.[^.]+$/,''));if(!name?.trim())return;try{if(selected===job.id){if(dirty)await saveEdits();releaseVideo();}const renamed=await api(`/api/jobs/${job.id}/rename-folder`,{folder_name:name});message('storage-status',`Folder renamed: ${renamed.folder_path}`);await poll();}catch(error){message('storage-status',error.message,true);}};card.append(rename);
    if(activeStates.has(job.state)){
      const cancel=document.createElement('button');cancel.className='retry cancel-button';cancel.textContent=job.state==='deleting'?'Deleting…':job.state==='canceling'?'Stopping…':'Cancel';cancel.disabled=['canceling','deleting'].includes(job.state);cancel.setAttribute('aria-label',`Cancel ${job.name}`);cancel.onclick=async event=>{event.stopPropagation();cancel.disabled=true;try{await api(`/api/jobs/${job.id}/cancel`);await poll();}catch(error){message('storage-status',error.message,true);cancel.disabled=false;}};card.append(cancel);
    }
    if(job.url && !activeStates.has(job.state)){
      const change=document.createElement('button');change.className='retry';change.textContent='Change quality';change.setAttribute('aria-label',`Change quality for ${job.name}`);change.onclick=event=>{event.stopPropagation();inspectYoutube(job);};card.append(change);
    }
    if(['error','cancelled'].includes(job.state) && (job.source||job.url)){
      const retry=document.createElement('button');retry.className='retry';retry.textContent=job.state==='cancelled'?'Resume':'Retry';retry.onclick=async event=>{event.stopPropagation();try{await api(`/api/jobs/${job.id}/retry`);await poll();}catch(error){message('upload-status',error.message,true);}};card.append(retry);
    }
    const remove=document.createElement('button');remove.className='retry delete-button';remove.textContent='Delete';remove.disabled=job.state==='deleting';remove.setAttribute('aria-label',`Delete ${job.name}`);remove.onclick=event=>{event.stopPropagation();deleteVideo(job);};card.append(remove,note);
    card.onclick=()=>selectJob(job.id);card.onkeydown=event=>{if(event.target===card && ['Enter',' '].includes(event.key)){event.preventDefault();selectJob(job.id);}};
    container.append(card);
  });
}
async function selectJob(id){
  if(id===selected){updateEditor();return;}
  if(dirty && selected){
    try{await saveEdits();}catch(error){message('editor-status',`Save your edits before switching videos: ${error.message}`,true);return;}
  }
  selected=id;dirty=false;lastState='';loadEditor();renderJobs();
}
function loadEditor(){
  const job=jobs.find(j=>j.id===selected);
  if(!job || !job.cues.length || job.state==='deleting'){$('editor').hidden=true;return;}
  edits=structuredClone(job.cues);
  if(job.detected_language==='en'||job.language==='en')edits.forEach(c=>{if(!c.english)c.english=c.source||'';});
  anchor=structuredClone(job.anchor||{x:50,y:job.position==='top'?15:90});
  $('subtitle-language').value=job.subtitle_language||'fa';
  const audioOnly=!job.metadata.width;
  $('video').pause();$('audio').pause();$('video').hidden=audioOnly;$('audio').hidden=!audioOnly;$('player').classList.toggle('audio-only',audioOnly);$('fullscreen').hidden=audioOnly;$('export').hidden=audioOnly;
  $('video').removeAttribute('src');$('audio').removeAttribute('src');$('editor').hidden=false;$('font-size').value=String(job.font_size||38);$('position').value=job.position||'bottom';
  media().src=asset(job,'preview');$('save-state').textContent='Edits saved on your laptop';message('editor-status','');renderCues();updateCaption();updateEditor();
}
function updateEditor(){
  const job=jobs.find(j=>j.id===selected);if(!job)return;
  if(job.cues.length && $('editor').hidden)loadEditor();
  const busy=activeStates.has(job.state);
  ['save','export','add-cue','font-size','position'].forEach(id=>$(id).disabled=busy);
  document.querySelectorAll('.cue input,.cue textarea,.cue .delete-cue').forEach(element=>element.disabled=busy);
  if(!busy&&lastState&&lastState!==job.state&&job.operation==='english'){edits=structuredClone(job.cues);renderCues();}
  $('export').textContent=busy?'Processing…':'Export subtitled video ↗';
  if(busy)message('editor-status',job.message);
  else if(lastState && lastState!==job.state)message('editor-status',job.message,job.state==='error');
  if(job.state==='complete'){
    $('downloads').hidden=false;$('download-video').href=asset(job,'video');$('download-srt').href=asset(job,'subtitles');
    const stale=dirty||job.export_stale;
    $('download-video').textContent=stale?'Download last export ↓':'Download video ↓';
    $('export-note').textContent=stale?'Your subtitles have changed. Export again to include them in the video.':'Your video has permanent subtitles. The SRT is also available separately.';
  }else if(job.state==='review'){
    $('downloads').hidden=false;$('download-video').removeAttribute('href');$('download-video').hidden=true;$('download-srt').href=asset(job,'subtitles');$('export-note').textContent='Review the subtitles, then export your video.';
  }else $('downloads').hidden=true;
  $('download-video').hidden=job.state!=='complete';
  if($('video').hidden)$('export-note').textContent='Save your edits, then download the selected language as text or timed subtitles.';
  lastState=job.state;languageUI();
}
function renderCues(){
  $('cue-count').textContent=`(${edits.length})`;$('cues').replaceChildren();
  edits.forEach((cue,index)=>{
    const row=document.createElement('div');row.className='cue';row.dataset.index=index;
    const top=document.createElement('div');top.className='cue-top';
    const play=document.createElement('button');play.textContent='▷';play.title='Play this subtitle';play.setAttribute('aria-label',`Play subtitle ${index+1}`);play.onclick=()=>{media().currentTime=Number(cue.start);media().play().catch(()=>{});};
    const label=document.createElement('span');label.textContent=String(index+1).padStart(2,'0');top.append(play,label);
    ['start','end'].forEach((key,i)=>{
      if(i){const arrow=document.createElement('span');arrow.textContent='→';top.append(arrow);}
      const input=document.createElement('input');input.type='number';input.min='0';input.step='.01';input.value=cue[key];input.setAttribute('aria-label',`Subtitle ${index+1} ${key} in seconds`);input.oninput=()=>{cue[key]=Number(input.value);setDirty();syncNativeCue(index);updateCaption();};top.append(input);
    });
    const remove=document.createElement('button');remove.className='delete-cue';remove.textContent='×';remove.title='Remove subtitle';remove.setAttribute('aria-label',`Remove subtitle ${index+1}`);remove.onclick=()=>{edits.splice(index,1);setDirty();renderCues();updateCaption();};top.append(remove);
    const text=document.createElement('textarea');text.lang=english()?'en':'fa';text.dir=english()?'ltr':'rtl';text.value=english()?(cue.english||''):cue.text;text.setAttribute('aria-label',`${english()?'English':'Persian'} subtitle ${index+1}`);text.oninput=()=>{cue[english()?'english':'text']=text.value;setDirty();syncNativeCue(index);updateCaption();};
    const source=document.createElement('div');source.className='source-text';source.textContent=cue.source||'';source.dir='auto';
    row.append(top,text,source);$('cues').append(row);
  });
  syncNativeCaptions();
}
function updateCaption(){
  const index=edits.findIndex(c=>media().currentTime>=Number(c.start)&&media().currentTime<Number(c.end));
  const video=$('video'), box=$('player').getBoundingClientRect();
  const ratio=video.videoWidth/video.videoHeight||16/9;
  const audioOnly=video.hidden;
  const w=audioOnly?box.width:Math.min(box.width,box.height*ratio);
  const h=audioOnly?Math.max(120,box.height-60):w/ratio;
  const plane=$('caption-plane');
  Object.assign(plane.style,{width:`${w}px`,height:`${h}px`,left:`${(box.width-w)/2}px`,top:audioOnly?'0px':`${(box.height-h)/2}px`});
  const caption=$('fixed-caption');
  caption.textContent=index<0?'':(english()?edits[index].english||'':edits[index].text);
  caption.dir=english()?'ltr':'rtl';caption.lang=english()?'en':'fa';
  Object.assign(caption.style,{left:`${anchor.x}%`,top:`${anchor.y}%`,fontSize:`${Math.max(12,Number($('font-size').value)*h/1080)}px`});
  document.querySelectorAll('.cue').forEach((row,i)=>row.classList.toggle('current',i===index));
}
function bodyEdits(){return {cues:edits,font_size:Number($('font-size').value),position:$('position').value,anchor,subtitle_language:$('subtitle-language').value};}
function languageUI(){
  const job=jobs.find(j=>j.id===selected);if(!job)return;
  const lang=$('subtitle-language').value;
  $('caption-language-label').textContent=english()?'English subtitles':'Persian subtitles';
  $('generate-english').hidden=!english()||edits.every(c=>c.english?.trim());
  $('generate-english').disabled=activeStates.has(job.state);
  $('download-srt').href=asset(job,'subtitles')+'&language='+lang;
  $('download-srt').textContent=`Download ${english()?'English':'Persian'} subtitles (.srt)`;
  $('download-text').href=asset(job,'transcript')+'&language='+lang;
}
async function saveEdits(){
  const body=bodyEdits();await api(`/api/jobs/${selected}/save`,body);const job=jobs.find(j=>j.id===selected);if(job)Object.assign(job,body);dirty=false;$('save-state').textContent='Edits saved on your laptop';updateEditor();
}
$('save').onclick=async()=>{try{await saveEdits();message('editor-status','Saved.');}catch(error){message('editor-status',error.message,true);}};
$('export').onclick=async()=>{
  if(!selected)return;try{await api(`/api/jobs/${selected}/export`,bodyEdits());dirty=false;$('save-state').textContent='Edits saved on your laptop';await poll();}catch(error){message('editor-status',error.message,true);}
};
$('add-cue').onclick=()=>{
  const job=jobs.find(j=>j.id===selected);const start=Math.max(0,Math.min(media().currentTime,job.metadata.duration-.1));
  const next=edits.find(c=>c.start>start);const previous=[...edits].reverse().find(c=>c.start<=start);
  const safeStart=previous?Math.max(start,previous.end):start;const end=Math.min(safeStart+3,next?next.start:job.metadata.duration);
  if(end<=safeStart){message('editor-status','There is no space here. Shorten or remove a nearby subtitle first.',true);return;}
  edits.push({start:Number(safeStart.toFixed(3)),end:Number(end.toFixed(3)),text:'متن زیرنویس',english:'Subtitle text',source:''});edits.sort((a,b)=>a.start-b.start);setDirty();renderCues();
};
$('font-size').onchange=()=>{setDirty();updateCaption();};
$('position').onchange=()=>{anchor={x:50,y:$('position').value==='top'?15:90};setDirty();updateCaption();};
for(const id of ['video','audio']){ $(id).addEventListener('timeupdate',updateCaption);$(id).addEventListener('loadedmetadata',updateCaption); }window.addEventListener('resize',updateCaption);
document.addEventListener('fullscreenchange',updateCaption);
new ResizeObserver(updateCaption).observe($('player'));
$('video').ondblclick=()=> $('fullscreen').click();
async function uploadNamedFiles(files){
  if(uploading || !files.length)return;uploading=true;uploadCancelled=false;$('drop').disabled=true;$('cancel-upload').hidden=false;
  try{
    for(let i=0;i<files.length;i++){
      if(uploadCancelled)break;
      const item=files[i],file=item.file;
      message('upload-status',`Adding video ${i+1} of ${files.length}: ${file.name}…`);
      uploadAbort=new AbortController();
      const response=await fetch(`/api/upload?name=${encodeURIComponent(file.name)}&language=${$('language').value}&folder_name=${encodeURIComponent(item.folderName)}`,{method:'POST',headers:{'Content-Type':'application/octet-stream','X-Studio-Token':token},body:file,signal:uploadAbort.signal});
      const result=await response.json();if(!response.ok)throw new Error(result.detail||'Upload failed.');
      await poll();if(!selected)await selectJob(result.id);
    }
    message('upload-status',uploadCancelled?'Upload cancelled. Already uploaded videos remain in the queue.':'Your videos are in the queue. Keep this launcher running while they process.');
  }catch(error){message('upload-status',uploadCancelled||error.name==='AbortError'?'Upload cancelled. Incomplete uploads are discarded.':error.message,!(uploadCancelled||error.name==='AbortError'));}finally{uploading=false;uploadAbort=null;$('drop').disabled=false;$('cancel-upload').hidden=true;await poll();}
}
$('cancel-upload').onclick=()=>{uploadCancelled=true;uploadAbort?.abort();};
$('drop').onclick=()=>$('picker').click();$('picker').onchange=event=>{const files=[...event.target.files];event.target.value='';uploadFiles(files);};
['dragenter','dragover'].forEach(type=>$('drop').addEventListener(type,event=>{event.preventDefault();if(!uploading)$('drop').classList.add('dragover');}));
['dragleave','drop'].forEach(type=>$('drop').addEventListener(type,event=>{event.preventDefault();$('drop').classList.remove('dragover');}));
$('drop').addEventListener('drop',event=>uploadFiles([...event.dataTransfer.files]));window.addEventListener('dragover',event=>event.preventDefault());window.addEventListener('drop',event=>event.preventDefault());
function resetYoutube(){youtubeInspection=null;reuseJobId=null;$('youtube-choices').hidden=true;$('download-youtube').disabled=true;}
async function inspectYoutube(existingJob=null){
  if(inspectingYoutube)return;
  inspectingYoutube=true;$('add-link').disabled=true;$('url').disabled=true;
  resetYoutube();
  try{
    if(dirty && selected)await saveEdits();
    if(existingJob){$('url').value=existingJob.url;$('language').value=existingJob.language;reuseJobId=existingJob.id;}
    message('upload-status','Checking the available video qualities…');
    const result=await api('/api/youtube/qualities',{url:$('url').value});
    youtubeInspection=result;$('youtube-folder').value=result.title||'YouTube video';$('youtube-title').textContent=result.title;
    $('youtube-quality').replaceChildren();
    const placeholder=document.createElement('option');placeholder.value='';placeholder.textContent='Select video quality';$('youtube-quality').append(placeholder);
    result.qualities.forEach(choice=>{const option=document.createElement('option');option.value=String(choice.height);option.textContent=`${choice.label} (${choice.width} × ${choice.frame_height})`;$('youtube-quality').append(option);});
    $('youtube-reuse').textContent=reuseJobId?'A new copy will keep your saved subtitles and edits.':'This choice applies to this video. Higher resolutions use more space.';
    $('youtube-choices').hidden=false;message('upload-status','Choose a quality, then download your video.');
    $('youtube-choices').scrollIntoView({behavior:'smooth',block:'center'});$('youtube-quality').focus();
  }catch(error){message('upload-status',error.message,true);}finally{inspectingYoutube=false;$('add-link').disabled=false;$('url').disabled=false;}
}
$('add-link').onclick=()=>inspectYoutube();
$('youtube-quality').onchange=()=>{$('download-youtube').disabled=!$('youtube-quality').value;$('download-youtube').textContent=$('youtube-quality').value?`Download ${$('youtube-quality').value}p & subtitle ↗`:'Download & subtitle ↗';};
$('download-youtube').onclick=async()=>{
  if(!youtubeInspection||!$('youtube-quality').value)return;
  $('download-youtube').disabled=true;$('add-link').disabled=true;$('url').disabled=true;$('youtube-quality').disabled=true;
  try{
    const job=await api('/api/youtube',{url:youtubeInspection.url,inspection_id:youtubeInspection.inspection_id,quality:$('youtube-quality').value,folder_name:$('youtube-folder').value,language:$('language').value,reuse_job_id:reuseJobId});
    $('url').value='';resetYoutube();message('upload-status',`Your ${job.quality_label} video is in the queue. The downloaded resolution will appear on its card.`);await poll();await selectJob(job.id);
  }catch(error){message('upload-status',error.message,true);}finally{$('download-youtube').disabled=!youtubeInspection||!$('youtube-quality').value;$('add-link').disabled=false;$('url').disabled=false;$('youtube-quality').disabled=false;}
};
$('url').addEventListener('input',resetYoutube);
$('url').addEventListener('keydown',event=>{if(event.key==='Enter'&&!inspectingYoutube)$('add-link').click();});
async function poll(){
  if(polling)return;polling=true;try{
    jobs=await api('/api/jobs',undefined,'GET');$('connection').hidden=true;renderJobs();
    if(removalWatch){
      const remaining=jobs.filter(job=>removalWatch.includes(job.id));
      if(!remaining.length){message('storage-status','Deleted. Studio video folders were moved to the Recycle Bin.');removalWatch=null;}
      else if(!remaining.some(job=>job.state==='deleting')){message('storage-status','Some videos could not be deleted. Close open video players, then try Delete again.',true);removalWatch=null;}
    }
    if(selected&&!jobs.some(job=>job.id===selected&&job.state!=='deleting'))releaseVideo();
    if(selected)updateEditor();else{const ready=jobs.find(j=>j.cues.length&&j.state!=='deleting');if(ready)await selectJob(ready.id);}
  }catch(error){$('connection').hidden=false;}finally{polling=false;}
}
window.addEventListener('beforeunload',event=>{if(dirty){event.preventDefault();event.returnValue='';}});
poll();setInterval(poll,2000);
async function loadStorage(){try{const health=await api('/health',undefined,'GET');if(health.api_version!=='named-folders-4'){$('update-notice').hidden=false;}const data=await api('/api/storage',undefined,'GET');$('storage-path').textContent=data.folder_path;}catch(error){message('storage-status',error.message,true);}}
$('open-storage').onclick=async()=>{$('open-storage').disabled=true;try{const result=await api('/api/open-storage');message('storage-status',`Opened in Windows File Explorer: ${result.folder_path}`);}catch(error){message('storage-status',error.message,true);}finally{$('open-storage').disabled=false;}};
$('copy-storage').onclick=()=>copyPath($('storage-path').textContent,$('storage-status'));
$('clear-all').onclick=async()=>{
  if(!jobs.length||!window.confirm(`Clear all ${jobs.length} videos from the studio? Processing will stop and studio video folders, subtitles and exports will move to the Recycle Bin. Browser download copies are separate.`))return;
  uploadCancelled=true;uploadAbort?.abort();releaseVideo();$('clear-all').disabled=true;
  try{removalWatch=jobs.map(job=>job.id);const result=await api('/api/jobs/clear');message('storage-status',`Removing ${result.deleting} videos from the studio…`);await poll();}
  catch(error){removalWatch=null;message('storage-status',error.message,true);$('clear-all').disabled=false;}
};
loadStorage();

$('subtitle-language').onchange=()=>{setDirty();renderCues();languageUI();};
$('generate-english').onclick=async()=>{try{await saveEdits();await api(`/api/jobs/${selected}/english`);await poll();}catch(error){message('editor-status',error.message,true);}};
$('fullscreen').onclick=async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await $('player').requestFullscreen();updateCaption();}catch(error){message('editor-status',error.message,true);}};
$('audio-drop').onclick=()=> $('audio-picker').click();
$('audio-picker').onchange=event=>{uploadFiles([...event.target.files]);event.target.value='';};
const draggable=$('fixed-caption');let dragging=false;
function moveCaption(event){const bounds=$('caption-plane').getBoundingClientRect();anchor={x:Math.max(2,Math.min(98,100*(event.clientX-bounds.left)/bounds.width)),y:Math.max(2,Math.min(98,100*(event.clientY-bounds.top)/bounds.height))};setDirty();updateCaption();}
draggable.onpointerdown=event=>{if(activeStates.has(jobs.find(j=>j.id===selected)?.state))return;dragging=true;draggable.setPointerCapture(event.pointerId);event.preventDefault();};
draggable.onpointermove=event=>{if(dragging)moveCaption(event);};
draggable.onpointerup=event=>{if(dragging){moveCaption(event);dragging=false;draggable.releasePointerCapture(event.pointerId);}};
draggable.onpointercancel=()=>dragging=false;
draggable.onkeydown=event=>{if(activeStates.has(jobs.find(j=>j.id===selected)?.state))return;const shifts={ArrowLeft:[-1,0],ArrowRight:[1,0],ArrowUp:[0,-1],ArrowDown:[0,1]};if(shifts[event.key]){event.preventDefault();const [x,y]=shifts[event.key];anchor={x:Math.max(2,Math.min(98,anchor.x+x)),y:Math.max(2,Math.min(98,anchor.y+y))};setDirty();updateCaption();}};

let pendingUploads=[];
function uploadFiles(files){
  if(uploading||!files.length)return;
  pendingUploads=files.map(file=>({file,folderName:file.name.replace(/\.[^.]+$/,'')}));
  $('upload-name-list').replaceChildren();
  pendingUploads.forEach((item,index)=>{const row=document.createElement('label');row.className='folder-name-row';const title=document.createElement('span');title.textContent=item.file.name;const input=document.createElement('input');input.type='text';input.maxLength=100;input.value=item.folderName;input.setAttribute('aria-label',`Folder name for ${item.file.name}`);input.oninput=()=>item.folderName=input.value;row.append(title,input);$('upload-name-list').append(row);});
  $('upload-names').hidden=false;$('start-named-upload').focus();
}
$('start-named-upload').onclick=()=>{const files=pendingUploads;pendingUploads=[];$('upload-names').hidden=true;uploadNamedFiles(files);};
$('dismiss-named-upload').onclick=()=>{pendingUploads=[];$('upload-names').hidden=true;};
