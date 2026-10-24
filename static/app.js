/* 拾光 Extract: browser behavior for link extraction and local Live Photo pairing */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const ui = {
    linkTab: $("tab-link"), liveTab: $("tab-live"), linkPane: $("pane-link"), livePane: $("pane-live"),
    linkForm: $("link-form"), liveForm: $("live-form"), url: $("share-link"),
    inspect: $("inspect-btn"), download: $("download-btn"), paste: $("paste-btn"),
    liveFiles: $("local-files"), drop: $("drop-zone"), selectedFiles: $("selected-files"),
    pair: $("pair-btn"), empty: $("result-empty"), content: $("result-content"),
    status: $("result-status"), overview: $("post-overview"), cover: $("post-thumbnail"),
    platform: $("post-platform"), title: $("post-title"), author: $("post-author"),
    progress: $("progress-box"), progressHead: $("progress-heading"), progressMessage: $("progress-message"),
    assets: $("asset-wrap"), assetCount: $("asset-count"), assetGrid: $("asset-grid"), zip: $("zip-button"),
    errorBox: $("error-box"), errorMessage: $("error-message"), retry: $("retry-btn"), toast: $("toast"),
  };
  let chosenFiles = [];
  let currentJob = null;
  let activeTimer = null;
  let busy = false;
  let lastAction = "link";
  const icons = {video: "i-video", image: "i-image", other: "i-file"};
  $("year").textContent = new Date().getFullYear();

  function notify(message) {
    ui.toast.textContent = message;
    ui.toast.classList.add("show");
    clearTimeout(notify.timer);
    notify.timer = setTimeout(() => ui.toast.classList.remove("show"), 3200);
  }
  function svg(id) {
    const e = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    const u = document.createElementNS("http://www.w3.org/2000/svg", "use");
    u.setAttribute("href", `#${id}`);
    e.appendChild(u);
    return e;
  }
  function hide(...els){els.forEach(el => el.classList.add("hidden"));}
  function show(...els){els.forEach(el => el.classList.remove("hidden"));}
  function setStatus(label, kind) {ui.status.textContent = label;ui.status.className = `status-tag ${kind || ""}`;}
  function setBusy(value) {
    busy = value;
    [ui.inspect, ui.download, ui.pair, $("import-btn")].forEach(button => button.disabled = value);
    ui.download.textContent = value ? "正在处理…" : "开始提取";
    if (!value) ui.download.appendChild(svg("i-arrow"));
    ui.pair.textContent = value ? "正在打包…" : "打包 Live Photo 原始素材";
    if (!value) ui.pair.appendChild(svg("i-arrow"));
  }
  function clearResult(){
    hide(ui.empty, ui.overview, ui.progress, ui.assets, ui.errorBox);
    show(ui.content);
  }
  function showProgress(head, message){
    clearResult();show(ui.progress);setStatus("处理中", "working");
    ui.progressHead.textContent = head;
    ui.progressMessage.textContent = message;
  }
  function showError(message) {
    clearResult();show(ui.errorBox);setStatus("提取失败", "error");
    ui.errorMessage.textContent = message;
    if (/小红书|网页导入|保存.*网页/.test(message)) $("page-import").open = true;
    setBusy(false);
    if (activeTimer){clearTimeout(activeTimer);activeTimer=null;}
  }
  function showOverview(data) {
    show(ui.content, ui.overview);hide(ui.empty);
    ui.platform.textContent = data.platform || "分享作品";
    ui.title.textContent = data.title || "已识别分享链接";
    ui.author.textContent = data.author || (data.preview_available === false ? "无法读取封面，仍可尝试提取" : "公开作品");
    ui.cover.hidden = !data.thumbnail;
    if (data.thumbnail) {
      ui.cover.referrerPolicy = "no-referrer";
      ui.cover.src = data.thumbnail;
      ui.cover.onerror = () => {ui.cover.hidden = true;};
    }
  }
  function currentMode(){return document.querySelector('input[name="media-mode"]:checked').value;}
  function getUrl() {
    const match = (ui.url.value || "").match(/https?:\/\/[^\s\u3000<>"'，。；;！!）)】\]]+/i);
    if (!match) throw new Error("请粘贴作品分享链接（包含 https://）。");
    return match[0];
  }
  async function postJson(endpoint, payload) {
    const res = await fetch(endpoint, {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `请求失败（${res.status}）`);
    return data;
  }
  async function inspectLink() {
    if (busy) return;
    let url;
    try {url = getUrl();} catch(e){notify(e.message);ui.url.focus();return;}
    setBusy(true);lastAction="link";showProgress("正在识别作品…","部分平台的图片和封面可能无法预览，但仍可尝试提取。");
    try {
      const data = await postJson("/api/inspect",{url});
      clearResult();showOverview(data);setStatus("已识别", "ready");
      if (!data.preview_available) {
        ui.author.textContent = data.message || "封面暂时不可用，可以直接尝试提取。";
        if (data.platform === "小红书") $("page-import").open = true;
      }
    } catch(err){showError(err.message || "识别失败，请检查链接。 ");}
    finally {setBusy(false);}
  }
  function niceSize(bytes){
    if(bytes < 1024) return `${bytes} B`;
    if(bytes < 1024*1024) return `${(bytes/1024).toFixed(0)} KB`;
    return `${(bytes/(1024*1024)).toFixed(1)} MB`;
  }
  function renderFile(file) {
    const item = document.createElement("div");item.className="asset-item";
    const preview = document.createElement("div");preview.className="asset-preview";
    if(file.type === "image" && !/\.(heic|heif|avif)$/i.test(file.name)){
      const img = document.createElement("img");img.loading="lazy";img.alt=file.name;
      img.src=file.preview_url;img.onerror=()=>{img.remove();preview.appendChild(svg("i-image"));};
      preview.appendChild(img);
    } else if(file.type === "video"){
      const video=document.createElement("video");video.src=file.preview_url;
      video.controls=true;video.preload="metadata";video.playsInline=true;video.muted=true;
      video.onerror=()=>{video.remove();preview.appendChild(svg("i-video"));};preview.appendChild(video);
    } else {preview.appendChild(svg(icons[file.type] || "i-file"));}
    const bottom = document.createElement("div");bottom.className="asset-foot";
    const name=document.createElement("b");name.textContent=file.name;name.title=file.name;
    const size=document.createElement("small");size.textContent=niceSize(file.size);
    const link=document.createElement("a");link.href=file.download_url;link.download=file.name;
    link.appendChild(svg("i-download"));link.appendChild(document.createTextNode(" 保存文件"));
    bottom.append(name,size,link);item.append(preview,bottom);return item;
  }
  function renderReady(job) {
    hide(ui.progress,ui.errorBox);show(ui.content,ui.assets);setStatus("提取完成", "ready");
    ui.assetCount.textContent=job.files.length;
    ui.assetGrid.replaceChildren(...job.files.map(renderFile));
    if(job.archive_url){ui.zip.href=job.archive_url;ui.zip.hidden=false;}
    else{ui.zip.hidden=true;}
    setBusy(false);
    notify(`成功获得 ${job.files.length} 个素材文件`);
  }
  async function pollJob(jobId, round=0) {
    if(jobId !== currentJob)return;
    if(round > 150){showError("任务等待时间过长，请稍后重新尝试。 ");return;}
    try{
      const response=await fetch(`/api/jobs/${encodeURIComponent(jobId)}`,{cache:"no-store"});
      const data=await response.json().catch(()=>({}));
      if(!response.ok)throw new Error(data.error||"无法获取任务状态。");
      if(data.status==="ready"){renderReady(data);return;}
      if(data.status==="error"){showError(data.message||"平台暂时不支持此作品。");return;}
      ui.progressHead.textContent = data.status==="queued" ? "正在排队…" : "正在提取素材…";
      ui.progressMessage.textContent=data.message || "请稍候，正在处理媒体文件。";
    }catch(err){
      if(round>4){showError(err.message||"网络连接已中断。");return;}
    }
    activeTimer=setTimeout(()=>pollJob(jobId,round+1),2600);
  }
  async function beginExtract(evt) {
    evt.preventDefault();if(busy)return;
    let url;try{url=getUrl();}catch(e){notify(e.message);ui.url.focus();return;}
    lastAction="link";setBusy(true);showProgress("正在提交提取任务…","公开作品解析可能需要几十秒，复杂视频会更久。");
    try{
      const info=await postJson("/api/jobs",{url,mode:currentMode()});
      currentJob=info.id;
      showOverview({platform:info.platform,title:"正在准备可下载的素材",author:"正在处理中…"});
      pollJob(info.id);
    }catch(err){showError(err.message||"提交失败，请重试。 ");}
  }
  function setTab(name){
    const isLink=name==="link";
    ui.linkTab.classList.toggle("active",isLink);ui.liveTab.classList.toggle("active",!isLink);
    ui.linkTab.setAttribute("aria-selected",String(isLink));ui.liveTab.setAttribute("aria-selected",String(!isLink));
    ui.linkPane.classList.toggle("hidden",!isLink);ui.livePane.classList.toggle("hidden",isLink);
  }
  function processFileList(files){
    chosenFiles=Array.from(files).slice(0,12);
    if(!chosenFiles.length){ui.selectedFiles.hidden=true;return;}
    ui.selectedFiles.hidden=false;
    ui.selectedFiles.textContent="";
    const summary=document.createElement("strong");summary.textContent=`已选择 ${chosenFiles.length} 个文件`;ui.selectedFiles.appendChild(summary);
    chosenFiles.forEach(file=>{const d=document.createElement("div");d.textContent=`· ${file.name}（${niceSize(file.size)}）`;ui.selectedFiles.appendChild(d);});
  }
  async function beginLive(evt){
    evt.preventDefault();if(busy)return;
    const photos=chosenFiles.filter(f=>/\.(heic|heif|jpe?g|png|webp|gif|avif)$/i.test(f.name));
    const videos=chosenFiles.filter(f=>/\.(mov|mp4|m4v|webm)$/i.test(f.name));
    if(!photos.length||!videos.length){notify("请同时选择至少一张照片和一段 MOV / MP4 视频。 ");return;}
    const total=chosenFiles.reduce((s,f)=>s+f.size,0);
    if(total>160*1024*1024){notify("单个任务最多支持 160 MB 素材。 ");return;}
    lastAction="live";setBusy(true);showProgress("正在打包原始素材…","原始照片和视频将保持原有格式，不会重新压缩画质。");
    try{
      const body=new FormData();chosenFiles.forEach(file=>body.append("files",file));
      const response=await fetch("/api/live-pair",{method:"POST",body});
      const data=await response.json().catch(()=>({}));
      if(!response.ok)throw new Error(data.error||"打包失败。 ");
      currentJob=data.id;
      showOverview({platform:"本地 Live Photo",title:"已保留原始素材",author:"文件包中保留 HEIC/JPG + MOV/MP4"});
      renderReady(data);
    }catch(err){showError(err.message||"请检查文件格式。 ");}
  }
  ui.linkTab.addEventListener("click",()=>setTab("link"));
  ui.liveTab.addEventListener("click",()=>setTab("live"));
  ui.linkForm.addEventListener("submit",beginExtract);
  ui.liveForm.addEventListener("submit",beginLive);
  ui.inspect.addEventListener("click",inspectLink);
  ui.retry.addEventListener("click",()=>{
    if(lastAction==="page")beginPageImport(new Event("submit"));
    else if(lastAction==="live")beginLive(new Event("submit"));
    else beginExtract(new Event("submit"));
  });
  async function beginPageImport(event) {
    event.preventDefault();if(busy)return;
    const file = $("saved-page").files[0];
    if(!file || !/\.html?$/i.test(file.name)){notify("请选择作品的 HTML 网页文件。");return;}
    if(file.size>5*1024*1024){notify("网页文件最大支持 5 MB。");return;}
    lastAction="page";setBusy(true);showProgress("正在读取作品网页…","读取图片信息后下载素材，网页文件不会保存。");
    try {
      const body=new FormData();body.append("file",file);
      const response=await fetch("/api/import-page",{method:"POST",body});
      const info=await response.json();
      if(!response.ok)throw new Error(info.error||"读取网页失败。");
      currentJob=info.id;showOverview(info);pollJob(info.id);
    } catch(error){showError(error.message||"网页导入失败。");}
  }
  $("page-form").addEventListener("submit",beginPageImport);
  ui.paste.addEventListener("click",async()=>{
    try{ui.url.value=await navigator.clipboard.readText();ui.url.focus();notify("链接已粘贴");}
    catch(_){ui.url.focus();notify("浏览器不允许读取剪贴板，请长按输入框手动粘贴。 ");}
  });
  document.querySelectorAll('input[name="media-mode"]').forEach(input=>{
    input.addEventListener("change",()=>document.querySelectorAll(".format-choice").forEach(label=>label.classList.toggle("active",label.querySelector("input").checked)));
  });
  ui.liveFiles.addEventListener("change",()=>processFileList(ui.liveFiles.files));
  ["dragover","dragenter"].forEach(event=>ui.drop.addEventListener(event,e=>{e.preventDefault();ui.drop.classList.add("dragging");}));
  ["dragleave","dragend"].forEach(event=>ui.drop.addEventListener(event,e=>{e.preventDefault();ui.drop.classList.remove("dragging");}));
  ui.drop.addEventListener("drop",e=>{e.preventDefault();ui.drop.classList.remove("dragging");processFileList(e.dataTransfer.files);});
})();
