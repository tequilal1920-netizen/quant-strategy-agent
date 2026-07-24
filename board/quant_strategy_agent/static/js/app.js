
(function(){
  "use strict";
  const $ = (id) => document.getElementById(id);
  const BASE = ((window.APP_BOOT||{}).basePath||"").replace(/\/$/,"");
  const S = {active:"data:macro", services:null, snapshot:null, seriesCache:{}, globalSupp:null, sw:null, cmdty:null, stockCode:null, stockOverride:null, kline:{health:null,history:[],stocks:[],dates:[],job:null,selectedJob:null}, factor:{status:null,history:null,detail:null,job:null,selectedJob:null}};
  let seq = 0;
  const TXT = {
    core:"\u6838\u5fc3\u7ed3\u8bba", loading:"\u6b63\u5728\u8f7d\u5165", noData:"\u6682\u65e0\u8db3\u591f\u8fde\u7eed\u6570\u636e", update:"\u66f4\u65b0", reset:"\u6062\u590d", open:"\u6253\u5f00",
    swPick:"\u53ef\u9009\u7533\u4e07\u4e00\u7ea7\u884c\u4e1a", cmdtyPick:"\u53ef\u9009\u5927\u5b97\u5546\u54c1", stockPick:"\u81ea\u9009\u6807\u7684", inputCode:"\u8f93\u5165A\u80a1\u4ee3\u7801",
    loadStock:"\u52a0\u8f7d\u6807\u7684", coreGroup:"\u6062\u590d\u6838\u5fc3\u7ec4\u5408", start:"\u5f00\u59cb", search:"\u641c\u7d22",
    latest:"\u6700\u65b0\u503c", rows:"\u884c", status:"\u72b6\u6001", code:"\u4ee3\u7801", name:"\u540d\u79f0", date:"\u65e5\u671f", source:"\u6765\u6e90",
    klineHome:"K\u7ebf\u8bb0\u5fc6\u5b66\u4e60", klineLearn:"\u5b66\u4e60\u8bb0\u5fc6", klineBacktest:"\u7b56\u7565\u56de\u6d4b", klineHistory:"\u5386\u53f2\u8bb0\u5f55",
    factorHome:"LLM\u56e0\u5b50\u6316\u6398", factorExpression:"\u56e0\u5b50\u8868\u8fbe\u5f0f", factorReport:"\u68c0\u9a8c\u62a5\u544a", factorScore:"\u7efc\u5408\u6253\u5206", factorMemory:"\u5386\u53f2\u8bb0\u5fc6"
  };
  const HEAD = {"kline:home":[TXT.klineHome,"\u5355\u80a1K\u7ebf\u8bb0\u5fc6\u5b66\u4e60"],"kline:learn":[TXT.klineLearn,"\u63d0\u4ea4\u5e76\u8ddf\u8e2a\u5b66\u4e60\u4efb\u52a1"],"kline:backtest":[TXT.klineBacktest,"\u6700\u65b0\u56de\u6d4b\u4e0e\u4fe1\u53f7"],"kline:history":[TXT.klineHistory,"\u5386\u53f2\u5b66\u4e60\u4efb\u52a1"],"factor:home":[TXT.factorHome,"\u4e25\u683c\u56e0\u5b50\u6316\u6398"],"factor:expression":[TXT.factorExpression,"\u5019\u9009\u56e0\u5b50\u8868\u8fbe\u5f0f"],"factor:report":[TXT.factorReport,"\u6eda\u52a8\u6837\u672c\u5916\u68c0\u9a8c"],"factor:score":[TXT.factorScore,"\u7efc\u5408\u8d28\u91cf\u8bc4\u5206"],"factor:memory":[TXT.factorMemory,"\u5386\u53f2\u8bb0\u5fc6"]};
  const COL = {code:TXT.code,name:TXT.name,industry:"\u884c\u4e1a",market:"\u5e02\u573a",region:"\u533a\u57df",symbol:"\u54c1\u79cd",close:"\u6536\u76d8",ret_1d:"\u65e5\u6536\u76ca",ret_5d:"5\u65e5\u6536\u76ca",ret_20d:"20\u65e5\u6536\u76ca",vol_20d:"20\u65e5\u6ce2\u52a8",mdd_60d:"60\u65e5\u56de\u64a4",mdd_20d:"20\u65e5\u56de\u64a4",as_of:TXT.date,source:TXT.source,title:"\u6807\u9898",published_at:"\u53d1\u5e03\u65f6\u95f4",event_type:"\u7c7b\u578b",url:"URL",qfq_close:"\u524d\u590d\u6743\u6536\u76d8",turnover:"\u6362\u624b\u7387",set:"\u96c6\u5408",total_return:"\u7d2f\u8ba1\u6536\u76ca",annual_return:"\u5e74\u5316\u6536\u76ca",max_drawdown:"\u6700\u5927\u56de\u64a4",sharpe:"\u590f\u666e",calmar:"Calmar\u6bd4\u7387",avg_position:"\u5e73\u5747\u4ed3\u4f4d",signal_trigger_count:"\u4fe1\u53f7\u6570",buy_hold_return:"\u4e70\u5165\u6301\u6709\u6536\u76ca",created_at:"\u521b\u5efa\u65f6\u95f4",analysis_depth:"\u5206\u6790\u6df1\u5ea6",holding_days:"\u6301\u6709\u5929\u6570",test_return:"\u6d4b\u8bd5\u6536\u76ca",view:"\u67e5\u770b",job_id:"\u4efb\u52a1ID",factor_view:"\u67e5\u770b",universe:"\u80a1\u7968\u6c60",target_accepted:"\u76ee\u6807\u901a\u8fc7\u6570",candidate_count:"\u5019\u9009\u6570",accepted_count:"\u901a\u8fc7\u6570",elapsed_seconds:"\u8017\u65f6\u79d2",chinese_name:"\u4e2d\u6587\u540d",channel:"\u6765\u6e90\u901a\u9053",test_rank_ic:"\u6d4b\u8bd5RankIC",valid_rank_ic:"\u9a8c\u8bc1RankIC",train_rank_ic:"\u8bad\u7ec3RankIC",rank_ic:"RankIC",lifecycle_deployment_confidence:"\u90e8\u7f72\u7f6e\u4fe1\u5ea6",redundancy_max_abs_corr:"\u6700\u5927\u5197\u4f59\u76f8\u5173",complexity:"\u590d\u6742\u5ea6",production_eligible:"\u53ef\u751f\u4ea7",lifecycle_state:"\u751f\u547d\u5468\u671f",test_period:"\u6d4b\u8bd5\u533a\u95f4",test:"\u6d4b\u8bd5",train_ic:"\u8bad\u7ec3IC",test_ic:"\u6d4b\u8bd5IC",decay:"\u8870\u51cf",year:"\u5e74\u4efd",group_spread:"\u5206\u7ec4\u6536\u76ca\u5dee",long_short_return:"\u591a\u7a7a\u6536\u76ca",long_return:"\u591a\u5934\u6536\u76ca",benchmark_return:"\u57fa\u51c6\u6536\u76ca",positive_ic_rate:"\u6b63IC\u6bd4\u4f8b",coverage:"\u8986\u76d6\u7387",proxy:"\u4ee3\u7406\u53d8\u91cf",formula:"\u8ba1\u7b97\u516c\u5f0f",logic:"\u7ecf\u6d4e\u903b\u8f91"};

  document.addEventListener("DOMContentLoaded", init);
  async function init(){
    bindNav();
    tick();
    setInterval(tick,30000);
    await Promise.allSettled([loadServices(), loadSnapshot()]);
    await render();
    loadPlotly().then((ready)=>{
      if(!ready) return;
      const redraw=()=>{ if(!document.querySelector('.nav-item.is-loading')) render(); };
      if('requestIdleCallback' in window) window.requestIdleCallback(redraw,{timeout:600});
      else window.setTimeout(redraw,0);
    });
    setInterval(loadServices,60000);
  }
  function loadPlotly(){
    if(window.Plotly) return Promise.resolve(true);
    if(window.__plotlyReady) return window.__plotlyReady;
    const url=((window.APP_BOOT||{}).plotlyUrl||'').trim();
    if(!url) return Promise.resolve(false);
    window.__plotlyReady=new Promise((resolve)=>{
      const script=document.createElement('script');
      script.src=url;
      script.async=true;
      script.onload=()=>resolve(Boolean(window.Plotly));
      script.onerror=()=>resolve(false);
      document.head.appendChild(script);
    });
    return window.__plotlyReady;
  }
  function bindNav(){ document.querySelectorAll(".nav-item").forEach(b=>b.addEventListener("click",async()=>{S.active=b.dataset.target; document.querySelectorAll(".nav-item").forEach(x=>x.classList.toggle("is-active",x===b)); await render();})); }
  function tick(){ $("service-clock").textContent = new Date().toLocaleString("zh-CN",{hour12:false}); }
  function apiPath(path){ if(/^https?:/i.test(path)) return path; return BASE + (path.startsWith("/")?path:"/"+path); }
  async function api(path,opt){ const r=await fetch(apiPath(path),Object.assign({cache:"default"},opt||{})); const t=await r.text(); let p={}; try{p=t?JSON.parse(t):{};}catch(_){p={raw:t};} if(!r.ok) throw new Error(p.message||p.error||p.data_state||("HTTP "+r.status)); return p; }
  function artifactPath(path){ return apiPath("/api/kline/artifact/"+String(path||"").replace(/^\/+/,"")); }
  async function loadServices(){ try{ S.services=await api("/api/services"); serviceBadges(); }catch(e){ $("service-badges").innerHTML='<span class="service-badge failed">service</span>'; } }
  async function loadSnapshot(){ try{ S.snapshot=await api("/api/board/snapshot"); stamps(S.snapshot); }catch(e){ conclusion("snapshot unavailable: "+esc(e.message)); } }
  async function fetchSeries(ids){ ids=Array.from(new Set(arr(ids).filter(Boolean))); const missing=ids.filter(id=>!S.seriesCache[id]); for(let i=0;i<missing.length;i+=18){ const batch=missing.slice(i,i+18); try{ const p=await api("/api/board/series?ids="+encodeURIComponent(batch.join(","))+"&frequency=raw"); arr(p.series).forEach(x=>{x.points=x.data||x.points||[]; S.seriesCache[x.id]=x;}); }catch(e){ batch.forEach(id=>{ if(!S.seriesCache[id]) S.seriesCache[id]={id,status:"failed",data:[]}; }); } } return ids.map(id=>S.seriesCache[id]).filter(Boolean); }
  async function hydrate(list){ const ids=arr(list).map(x=>x&&x.id).filter(Boolean); const got=await fetchSeries(ids); const map=new Map(got.map(x=>[x.id,x])); return arr(list).map(x=>Object.assign({},x,map.get(x.id)||{})); }
  function serviceBadges(){ const m=(S.services&&S.services.services)||{}; const n={board:"\u6570\u636e",kline:"K\u7ebf",factor:"\u56e0\u5b50"}; $("service-badges").innerHTML=Object.keys(n).map(k=>`<span class="service-badge ${st((m[k]||{}).status||(m[k]||{}).snapshot_status)}">${n[k]}</span>`).join(""); }
  function stamps(x){ $("as-of").textContent=(x&&x.as_of)||"--"; $("generated-at").textContent=(x&&x.generated_at)||"--"; }
  async function render(){ seq=0; const [g,v]=S.active.split(":"); if(g==="data") return await renderData(v); if(g==="kline") return await renderKline(v); if(g==="factor") return await renderFactor(v); }
  function header(title,sub,eye){ $("page-title").textContent=title||"--"; $("page-subtitle").textContent=sub||""; $("page-eyebrow").textContent=eye||"\u7814\u7a76\u603b\u89c8"; }
  function root(h){ $("view-root").innerHTML=h; }
  function conclusion(h){ $("core-conclusion").innerHTML=`<span class="eyebrow">${TXT.core}</span><p>${h}</p>`; }
  function mod(k){ return ((((S.snapshot||{}).modules)||{})[k])||{}; } function ser(k){ return arr(mod(k).series); } function tabs(k){ return arr(mod(k).tables); } function table(k,id){ return tabs(k).find(x=>x.id===id||x.title===id)||{}; }
  function arr(x){ return Array.isArray(x)?x:[]; } function obj(x){ return x&&typeof x==="object"&&!Array.isArray(x)?x:{}; } function esc(x){ return String(x??"").replace(/[&<>"']/g,m=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[m])); }
  function st(x){ x=String(x||"").toLowerCase(); if(["ok","live","ready","done","completed","passed","pass"].includes(x)) return "ok"; if(["queued","running","cancelling"].includes(x)) return "running"; return "failed"; }
  function pts(s,max){ const out=arr(s&&(s.data||s.points)).map(p=>{p=obj(p); const x=p.date||p.as_of||p.trade_date||p.x; const n=Number(p.value??p.close??p.y); return x&&Number.isFinite(n)?{date:String(x),value:n}:null;}).filter(Boolean).sort((a,b)=>a.date.localeCompare(b.date)); const d=[]; out.forEach(p=>{ if(d.length&&d[d.length-1].date===p.date)d[d.length-1]=p; else d.push(p); }); return max?d.slice(-max):d; }
  function latest(s){ const p=pts(s); return p[p.length-1]||null; } function prev(s){ const p=pts(s); return p.length>1?p[p.length-2]:null; }
  function fmt(v,d=2){ const n=Number(v); if(!Number.isFinite(n)) return "--"; return new Intl.NumberFormat("zh-CN",{maximumFractionDigits:d}).format(n); } function signed(v){ const n=Number(v); return Number.isFinite(n)?(n>0?"+":"")+fmt(n,2):"--"; }
  function sourceText(v){ const s=String(v??""); if(s==="account") return "\u8d26\u6237\u8bb0\u5f55"; if(s==="server"||s==="server_run") return "\u670d\u52a1\u7aef\u8bb0\u5f55"; if(s.includes("Eastmoney")) return "\u4e1c\u65b9\u8d22\u5bcc\u5168\u7403\u6307\u6570K\u7ebfAPI"; if(s.includes("Yahoo")) return "\u96c5\u864e\u8d22\u7ecf\u56fe\u8868API"; if(s.includes("AKShare")) return s.replace("AKShare","AKShare\u514d\u8d39\u91d1\u878d\u6570\u636e\u63a5\u53e3"); return s; }
  function valueText(v){ const s=String(v??""); const m={yes:"\u662f",no:"\u5426",true:"\u662f",false:"\u5426",ready:"\u5df2\u5c31\u7eea",check:"\u5f85\u68c0\u67e5",running:"\u8fd0\u884c\u4e2d",queued:"\u6392\u961f\u4e2d",ok:"\u6b63\u5e38",done:"\u5b8c\u6210",completed:"\u5b8c\u6210",failed:"\u5931\u8d25",error:"\u9519\u8bef",available:"\u53ef\u7528",accepted:"\u901a\u8fc7",rejected:"\u672a\u901a\u8fc7",train:"\u8bad\u7ec3\u96c6",valid:"\u9a8c\u8bc1\u96c6",test:"\u6d4b\u8bd5\u96c6",full:"\u5168\u6837\u672c",fast:"\u5feb\u901f",standard:"\u6807\u51c6",deep:"\u6df1\u5ea6",balanced:"\u5e73\u8861",conservative:"\u4fdd\u5b88",aggressive:"\u79ef\u6781",ALL_A:"\u5168A"}; if(s==="nested_orthogonal_complement_seed") return "\u5d4c\u5957\u6b63\u4ea4\u8865\u5145\u79cd\u5b50"; if(s==="llm_hypothesis_generation") return "LLM\u5047\u8bbe\u751f\u6210"; return m[s]||m[s.toLowerCase()]||s; }
  function seriesLabel(s){ const raw=String((s&& (s.label||s.name||s.id))||"\u6307\u6807"); const m={"SSE Composite close":"\u4e0a\u8bc1\u7efc\u6307\u6536\u76d8","CSI 300 close":"\u6caa\u6df1300\u6536\u76d8","S&P 500 close":"\u6807\u666e500\u6536\u76d8","NASDAQ close":"\u7eb3\u65af\u8fbe\u514b\u6536\u76d8","Dow Jones close":"\u9053\u743c\u65af\u6536\u76d8","Hang Seng close":"\u6052\u751f\u6307\u6570\u6536\u76d8","KOSPI close":"\u97e9\u56fdKOSPI\u6536\u76d8","Nikkei 225 close":"\u65e5\u7ecf225\u6536\u76d8","Euro Stoxx 50 close":"\u6b27\u6d32\u65af\u6258\u514b50\u6536\u76d8","DAX close":"\u5fb7\u56fdDAX\u6536\u76d8"}; return m[raw]||raw.replace(/\bclose\b/ig,"\u6536\u76d8").replace(/\bright\b/ig,"\u53f3\u8f74"); }
  function maybe(v,c){ if(v===null||v===undefined||v==="") return "--"; if(c==='source') return sourceText(v); if(c&&(["code","symbol","event_id"].includes(c)||/(^|_)id$/i.test(c))) return String(v); if(typeof v==="number") return fmt(v,2); if(typeof v==="boolean") return v?"\u662f":"\u5426"; if(/^-?\d+(\.\d+)?$/.test(String(v))&&String(v).length<12) return fmt(Number(v),2); return valueText(v); }
  function trend(s){ const a=latest(s), b=prev(s), name=esc(seriesLabel(s)); if(!a) return name+" \u6682\u65e0\u6570\u636e"; if(!b) return `${name} ${fmt(a.value,2)}${esc(s.unit||"")}`; const d=a.value-b.value; return `${name} ${fmt(a.value,2)}${esc(s.unit||"")} (${signed(d)})`; }
  function cardHTML(items){ return `<div class="kpi-grid">${items.map(it=>{ const s=it.series||it, p=it.value!==undefined?{value:it.value,date:it.as_of}:latest(s), b=it.value!==undefined?null:prev(s), ch=it.change!==undefined?it.change:(p&&b?p.value-b.value:null); return `<article class="kpi-card"><small>${esc(it.label||s.label||s.name||"\u6307\u6807")}</small><strong>${fmt(p&&p.value,2)} ${esc(it.unit||s.unit||"")}</strong><em><span>${ch===null||ch===undefined?TXT.latest:signed(ch)}</span><span>${esc((p&&p.date)||it.as_of||s.as_of||"")}</span></em></article>`;}).join("")}</div>`; }
  function pid(p){ seq++; return (p||"p")+seq; } function panel(id,t,s,w){ return `<section class="chart-panel ${w?"wide":""}"><div class="panel-header"><div><h3>${esc(t)}</h3>${s?`<p>${esc(s)}</p>`:""}</div></div><div id="${id}" class="plot-frame"></div></section>`; }
  function plot(id,traces,layout){ const e=$(id); if(!e) return; if(!window.Plotly||!traces.length){ e.innerHTML=`<div class="chart-fallback">${TXT.noData}</div>`; return; } Plotly.newPlot(e,traces,Object.assign({font:{family:'Arial,"KaiTi","Microsoft YaHei",sans-serif',size:10,color:'#344054'},paper_bgcolor:'rgba(0,0,0,0)',plot_bgcolor:'rgba(0,0,0,0)',margin:{l:44,r:14,t:12,b:40}},layout||{}),{responsive:true,displayModeBar:false}); }
  function line(id,list,opt){ opt=Object.assign({max:220,rebase:false},opt||{}); const traces=arr(list).slice(0,6).map(s=>{ let p=pts(s,opt.max); if(opt.rebase&&p.length){ const base=p.find(x=>x.value!==0); if(base) p=p.map(x=>({date:x.date,value:x.value/base.value*100})); } return {type:'scatter',mode:'lines',connectgaps:true,name:seriesLabel(s),x:p.map(x=>x.date),y:p.map(x=>x.value),line:{width:2}}; }).filter(t=>t.x.length>=1); plot(id,traces,{hovermode:'x unified',legend:{orientation:'h',y:-0.22,font:{size:10}},yaxis:{gridcolor:'#edf0f2',zerolinecolor:'#d8e0e7'},xaxis:{showgrid:false}}); }
  function lineSmart(id,list,opt){ opt=Object.assign({max:220,rebase:false},opt||{}); const units=Array.from(new Set(arr(list).map(s=>s.unit||'').filter(Boolean))); const primary=units[0]||''; const traces=arr(list).slice(0,6).map((s,i)=>{ let p=pts(s,opt.max); if(opt.rebase&&p.length){ const base=p.find(x=>x.value!==0); if(base) p=p.map(x=>({date:x.date,value:x.value/base.value*100})); } const right=!opt.rebase&&units.length>1&&s.unit!==primary; return {type:'scatter',mode:'lines',connectgaps:true,name:`${seriesLabel(s)}${s.unit?' - '+s.unit:''}${right?' - \u53f3\u8f74':''}`,x:p.map(x=>x.date),y:p.map(x=>x.value),yaxis:right?'y2':'y',line:{width:2,color:['#2f75b5','#b42318','#168a47','#c46a08'][i%4]}}; }).filter(t=>t.x.length>=1); const layout={hovermode:'x unified',legend:{orientation:'h',y:-0.26,font:{size:10}},yaxis:{title:primary,gridcolor:'#edf0f2',zerolinecolor:'#d8e0e7'},xaxis:{showgrid:false}}; if(units.length>1&&!opt.rebase) layout.yaxis2={title:units.filter(u=>u!==primary).join('/'),overlaying:'y',side:'right',showgrid:false,zeroline:false}; plot(id,traces,layout); }
  function bar(id,rows){ const d=rows.filter(r=>Number.isFinite(Number(r.value))); plot(id,d.length?[{type:'bar',x:d.map(r=>r.label),y:d.map(r=>Number(r.value)),marker:{color:d.map(r=>Number(r.value)>=0?'#168a47':'#c00000')},text:d.map(r=>fmt(r.value)),textposition:'auto'}]:[],{showlegend:false,xaxis:{tickangle:-25,showgrid:false},yaxis:{gridcolor:'#edf0f2',zerolinecolor:'#d8e0e7'}}); }
  function scatter(id,rows){ const d=rows.filter(r=>Number.isFinite(Number(r.x))&&Number.isFinite(Number(r.y))); plot(id,d.length?[{type:'scatter',mode:'markers+text',x:d.map(r=>+r.x),y:d.map(r=>+r.y),text:d.map(r=>r.label),textposition:'top center',marker:{size:10,color:'#2f75b5',opacity:.78}}]:[],{showlegend:false,xaxis:{gridcolor:'#edf0f2',zerolinecolor:'#d8e0e7'},yaxis:{gridcolor:'#edf0f2',zerolinecolor:'#d8e0e7'}}); }
  function tableHTML(title,rows,cols){ rows=arr(rows).slice(0,120); cols=cols&&cols.length?cols:Object.keys(obj(rows[0])).slice(0,10); return `<section class="table-panel"><div class="panel-header"><div><h3>${esc(title)}</h3><p>${rows.length} ${TXT.rows}</p></div></div><div class="table-scroll"><table class="data-table"><thead><tr>${cols.map(c=>`<th>${esc(COL[c]||c)}</th>`).join("")}</tr></thead><tbody>${rows.map(r=>`<tr>${cols.map(c=>cell(r,c)).join("")}</tr>`).join("")}</tbody></table></div></section>`; }
  function cell(r,c){ const v=obj(r)[c]; if(c==='view'&&r.job_id) return `<td><a href="#" data-kline-view="${esc(r.job_id)}">\u67e5\u770b</a></td>`; if(c==='factor_view'&&r.job_id) return `<td><a href="#" data-factor-view="${esc(r.job_id)}">\u67e5\u770b</a></td>`; if(c==='job_id'&&v) return `<td><a href="#" data-job-id="${esc(v)}">${esc(v)}</a></td>`; if(c==='url'&&v) return `<td><a href="${esc(v)}" target="_blank" rel="noreferrer">${TXT.open}</a></td>`; return `<td>${esc(maybe(v,c))}</td>`; }
  function pick(rows,want,key,n){ const a=[]; want.forEach(name=>{ const r=rows.find(x=>String(x[key]||"").includes(name)); if(r&&!a.includes(r[key])) a.push(r[key]); }); rows.forEach(r=>{ if(a.length<n&&!a.includes(r[key])) a.push(r[key]); }); return a.slice(0,n); } function digits(x){ return String(x||"").replace(/\D/g,""); }

  async function renderData(k){ const m=mod(k),titles={macro:'\u4e2d\u56fd\u5b8f\u89c2',global_markets:'\u5168\u7403\u5e02\u573a',sw_industries:'\u7533\u4e07\u4e00\u7ea7\u884c\u4e1a',commodities:'\u5927\u5b97\u5546\u54c1',stock:'\u4e2a\u80a1\u884c\u60c5',news_events:'\u65b0\u95fb\u4e0e\u4e8b\u4ef6'}; header(titles[k]||m.title||k,"","\u7814\u7a76\u603b\u89c8"); stamps(S.snapshot); if(k==='macro') return await macro(); if(k==='global_markets') return await global(); if(k==='sw_industries') return await sw(); if(k==='commodities') return await cmdty(); if(k==='stock') return await stock(); if(k==='news_events') return await news(); }
  function maxDate(items){ let m=""; arr(items).forEach(x=>{ const d=(x&& (x.as_of||x.date||x.published_at))||""; if(String(d)>m)m=String(d);}); return m||"--"; }
  function avg(rows,k){ const v=arr(rows).map(r=>Number(r[k])).filter(Number.isFinite); return v.length?v.reduce((a,b)=>a+b,0)/v.length:null; }
  function firstPointSeries(list,n){ return arr(list).filter(s=>pts(s).length>1).slice(0,n||4); }
  function splitTwo(list){ list=arr(list).filter(s=>pts(s).length>1); let pct=list.filter(s=>String(s.unit||'').includes('%')||/yoy|mom|ret|vol|drawdown|mdd|cpi|ppi|pmi/.test(String(s.id+s.label).toLowerCase())); let lev=list.filter(s=>!pct.includes(s)); if(!lev.length||!pct.length){ const mid=Math.ceil(Math.min(list.length,8)/2); lev=list.slice(0,mid); pct=list.slice(mid,mid+4); } return [lev.slice(0,4), (pct.length?pct:lev.slice(0,4)).slice(0,4)]; }
  function regionLine(rows,key){ const order=[['A\u80a1',['China A','A','\u4e2d\u56fdA\u80a1','\u4e0a\u8bc1','\u6caa\u6df1']],['\u7f8e\u80a1',['United States','US','\u7f8e\u56fd','S&P','NASDAQ','Dow']],['\u6e2f\u80a1',['Hong Kong','HK','\u4e2d\u56fd\u9999\u6e2f','\u6052\u751f']],['\u97e9\u80a1',['\u97e9\u80a1','KOSPI','\u97e9']],['\u65e5\u80a1',['\u65e5\u80a1','Nikkei','\u65e5']],['\u6b27\u80a1',['\u6b27\u80a1','DAX','Stoxx','\u6b27']]]; return order.map(([name,regs])=>{ const rs=rows.filter(r=>regs.some(reg=>String(r.region||'').includes(reg)||String(r.market||'').includes(reg))); const v=avg(rs,key); const n=rs.length; return `${name} ${v===null?'n/a':signed(v)+'%'}${n?`(${n})`:''}`; }).join('; '); }

  function pickRegionSeries(list){
    const groups=[['A\u80a1',/(\u4e0a\u8bc1|\u6caa\u6df1|SSE|CSI)/i],['\u7f8e\u80a1',/(\u6807\u666e|S&P|SPX)/i],['\u6e2f\u80a1',/(\u6052\u751f\u6307\u6570|Hang Seng)/i],['\u97e9\u80a1',/(KOSPI|\u97e9\u56fd)/i],['\u65e5\u80a1',/(Nikkei|\u65e5\u7ecf)/i],['\u6b27\u80a1',/(Euro Stoxx|DAX|\u6b27\u6d32)/i]];
    const labelMap={"\u0041\u80a1":"\u4e0a\u8bc1\u7efc\u6307\u6536\u76d8","\u7f8e\u80a1":"\u6807\u666e500\u6536\u76d8","\u6e2f\u80a1":"\u6052\u751f\u6307\u6570\u6536\u76d8","\u97e9\u80a1":"\u97e9\u56fd\u7efc\u5408\u6307\u6570\u6536\u76d8","\u65e5\u80a1":"\u65e5\u7ecf225\u6536\u76d8","\u6b27\u80a1":"\u6b27\u6d32\u65af\u6258\u514b50\u6536\u76d8"};
    const used=new Set(), out=[];
    groups.forEach(([name,rx])=>{ const s=arr(list).find(x=>!used.has(x.id)&&rx.test(String(x.label||x.name||x.id||x.market||''))&&pts(x).length>=1); if(s){used.add(s.id); out.push(Object.assign({},s,{label:name+'\u4ee3\u8868 - '+(labelMap[name]||seriesLabel(s))}));} });
    arr(list).forEach(s=>{ if(out.length<6&&!used.has(s.id)&&pts(s).length>=1){used.add(s.id); out.push(s);} });
    return out.slice(0,6);
  }

  async function aiFill(module,subject,context,target){ const el=$(target); if(!el)return; el.innerHTML='<p>AI\u5206\u6790\u751f\u6210\u4e2d...</p>'; try{ const r=await api('/api/ai/analyze',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({module,subject,context})}); el.innerHTML=r.html||'<p>\u6682\u65e0AI\u5206\u6790\u7ed3\u679c\u3002</p>'; }catch(e){ el.innerHTML='<p class="ai-red">AI\u6682\u4e0d\u53ef\u7528\uff1a'+esc(e.message)+'</p>'; } }
  async function getGlobalSupp(){ if(!S.globalSupp){ try{S.globalSupp=await api('/api/market/global_supplement');}catch(_){S.globalSupp={rows:[],series:[]};} } return S.globalSupp; }

  async function macro(){ const meta=ser('macro'); const cardIds=['cn_gdp_yoy','cn_cpi_yoy','cn_ppi_yoy','cn_m2_yoy']; const cards=await fetchSeries(cardIds); const top=[cards.find(s=>s.id==='cn_gdp_yoy'),cards.find(s=>s.id==='cn_cpi_yoy'),cards.find(s=>s.id==='cn_m2_yoy')].filter(Boolean); conclusion(`\u622a\u81f3 ${maxDate(top)}, ${top.map(trend).join('; ')}.`); const groups=[...new Set(meta.map(s=>s.submodule).filter(Boolean))].slice(0,8); const html=[cardHTML(cards.map(s=>({series:s})))], draw=[]; for(const g of groups){ const candidates=meta.filter(s=>s.submodule===g&&s.as_of).slice(0,8); const full=await hydrate(candidates); const [a,b]=splitTwo(full); if(!a.length&&!b.length) continue; const id1=pid('m'),id2=pid('m'); html.push(`<div class="section-heading"><div><span class="eyebrow">\u5b8f\u89c2\u5206\u9879</span><h2>${esc(g)}</h2><p>\u622a\u81f3 ${maxDate(full)}, ${firstPointSeries(full,2).map(trend).join('; ')}.</p></div></div><div class="panel-grid">${panel(id1,g+' \u6c34\u5e73\u6307\u6807','\u91cf\u7eb2\u4e0d\u540c\u81ea\u52a8\u53f3\u8f74',false)}${panel(id2,g+' \u540c\u6bd4/\u8109\u51b2\u6307\u6807','\u767e\u5206\u6bd4\u6216\u6269\u6563\u6307\u6807',false)}</div>`); draw.push(()=>lineSmart(id1,a,{max:260}),()=>lineSmart(id2,b,{max:260})); } root(html.join('')); draw.forEach(f=>f()); }

  async function global(){ const baseRows=arr(table('global_markets','global_market_matrix').rows); const supp=await getGlobalSupp(); const rowMap=new Map(); arr(supp.rows).concat(baseRows).forEach(r=>{ if(!rowMap.has(r.market)) rowMap.set(r.market,r); }); const rows=Array.from(rowMap.values()); const asof=maxDate(rows); conclusion(`<span>\u622a\u81f3 ${asof}, \u6700\u8fd1\u4e00\u65e5\uff1a ${regionLine(rows,'ret_1d')}.</span><br><span>\u6700\u8fd1\u4e00\u5468\uff1a ${regionLine(rows,'ret_5d')}\uff1b\u5408\u8ba1 ${rows.length} \u4e2a\u6307\u6570\u3002</span>`); const closeMeta=ser('global_markets').filter(s=>/_close$/.test(s.id)); const close=(await hydrate(closeMeta)).concat(arr(supp.series)); const best=[...rows].sort((a,b)=>+b.ret_5d-+a.ret_5d)[0]||{}, worst=[...rows].sort((a,b)=>+a.ret_5d-+b.ret_5d)[0]||{}; const p1=pid('g'),p2=pid('g'),p3=pid('g'),p4=pid('g'); root(cardHTML([{label:'\u5468\u5ea6\u6700\u5f3a',value:best.ret_5d,unit:'%',as_of:best.market},{label:'\u5468\u5ea6\u6700\u5f31',value:worst.ret_5d,unit:'%',as_of:worst.market},{label:'\u6307\u6570\u6570\u91cf',value:rows.length,unit:'',as_of:asof},{label:'\u8865\u5145\u6307\u6570',value:arr(supp.rows).length,unit:''}])+`<div class="panel-grid">${panel(p1,'\u4e3b\u8981\u6307\u6570\u8d70\u52bf','\u57fa\u671f=100',true)}${panel(p2,'\u6700\u8fd1\u4e00\u65e5\u6536\u76ca','A\u80a1 / \u7f8e\u80a1 / \u6e2f\u80a1 / \u97e9\u80a1 / \u65e5\u80a1 / \u6b27\u80a1',false)}${panel(p3,'\u6700\u8fd1\u4e00\u5468\u6536\u76ca','A\u80a1 / \u7f8e\u80a1 / \u6e2f\u80a1 / \u97e9\u80a1 / \u65e5\u80a1 / \u6b27\u80a1',false)}${panel(p4,'\u98ce\u9669\u6536\u76ca\u56fe','\u6a2a\u8f7420\u65e5\u6ce2\u52a8\uff0c\u7eb5\u8f7420\u65e5\u6536\u76ca',false)}</div>`+tableHTML('\u5168\u7403\u5e02\u573a\u77e9\u9635',rows,['market','region','close','ret_1d','ret_5d','ret_20d','vol_20d','mdd_60d','as_of','source'])); line(p1,pickRegionSeries(close),{rebase:true,max:180}); bar(p2,rows.map(r=>({label:r.market,value:r.ret_1d}))); bar(p3,rows.map(r=>({label:r.market,value:r.ret_5d}))); scatter(p4,rows.map(r=>({label:r.market,x:r.vol_20d,y:r.ret_20d}))); }

  async function sw(){ const rows=arr(table('sw_industries','sw_l1_full_snapshot').rows); if(!S.sw){ const wantCodes=['801080','801770','801750','801730','801150','801050','801780','801120']; S.sw=rows.filter(r=>wantCodes.includes(digits(r.code))).map(r=>r.industry); if(!S.sw.length) S.sw=pick(rows,[], 'industry',8); } const sel=rows.filter(r=>S.sw.includes(r.industry)); const asof=maxDate(sel.length?sel:rows); const best1=[...sel].sort((a,b)=>+b.ret_1d-+a.ret_1d)[0]||{}, worst1=[...sel].sort((a,b)=>+a.ret_1d-+b.ret_1d)[0]||{}; const best5=[...sel].sort((a,b)=>+b.ret_5d-+a.ret_5d)[0]||{}, worst5=[...sel].sort((a,b)=>+a.ret_5d-+b.ret_5d)[0]||{}; conclusion(`<span>\u622a\u81f3 ${asof}, \u6700\u8fd1\u4e00\u65e5\u6838\u5fc3\u5747\u503c ${signed(avg(sel,'ret_1d'))}%, \u6700\u5f3a ${esc(best1.industry)} ${signed(best1.ret_1d)}%, \u6700\u5f31 ${esc(worst1.industry)} ${signed(worst1.ret_1d)}%.</span><br><span>\u6700\u8fd1\u4e00\u5468\u6838\u5fc3\u5747\u503c ${signed(avg(sel,'ret_5d'))}%, \u6700\u5f3a ${esc(best5.industry)} ${signed(best5.ret_5d)}%, \u6700\u5f31 ${esc(worst5.industry)} ${signed(worst5.ret_5d)}%.</span>`); const p1=pid('sw'),p2=pid('sw'); const html=[control('sw-select',TXT.swPick,rows,'industry',S.sw,'sw-apply','sw-reset'),cardHTML([{label:'\u5df2\u9009\u884c\u4e1a',value:sel.length,unit:'',as_of:asof},{label:'\u65e5\u5747\u6da8\u8dcc',value:avg(sel,'ret_1d'),unit:'%'},{label:'\u5468\u5747\u6da8\u8dcc',value:avg(sel,'ret_5d'),unit:'%'},{label:'20\u65e5\u6ce2\u52a8\u5747\u503c',value:avg(sel,'vol_20d'),unit:'%'}]),`<div class="panel-grid">${panel(p1,'\u6838\u5fc3\u884c\u4e1a\u76f8\u5bf9\u8d70\u52bf','\u57fa\u671f=100',true)}${panel(p2,'\u6838\u5fc3\u884c\u4e1a\u5468\u5ea6\u6536\u76ca','\u968f\u63a7\u4ef6\u66f4\u65b0',false)}</div>`]; const draw=[()=>line(p1,[],{}),()=>bar(p2,sel.map(r=>({label:r.industry,value:r.ret_5d})))]; const allSeries=[]; for(const r of sel){ const c=digits(r.code); const series=await fetchSeries([`sw_${c}_close`,`sw_${c}_ret_20d`,`sw_${c}_vol_20d`,`sw_${c}_drawdown_60d`]); allSeries.push({r,series}); }
    draw[0]=()=>line(p1,allSeries.map(x=>x.series.find(s=>/_close$/.test(s.id))).filter(Boolean),{rebase:true,max:160}); for(const item of allSeries){ const id1=pid('sgi'), id2=pid('sgi'); html.push(`<div class="section-heading"><div><span class="eyebrow">\u884c\u4e1a\u666f\u6c14</span><h2>${esc(item.r.industry)}</h2><p>\u622a\u81f3 ${esc(item.r.as_of||asof)}, 1d ${signed(item.r.ret_1d)}%, 1w ${signed(item.r.ret_5d)}%, 20d ${signed(item.r.ret_20d)}%.</p></div></div><div class="panel-grid">${panel(id1,item.r.industry+' \u4ef7\u683c\u8d70\u52bf\u4e0e20\u65e5\u6536\u76ca','\u5de6\u8f74\u4ef7\u683c/\u57fa\u51c6\uff0c\u53f3\u8f74\u6536\u76ca',false)}${panel(id2,item.r.industry+' \u6ce2\u52a8\u4e0e\u56de\u64a4','\u666f\u6c14\u98ce\u9669\u6e29\u5ea6',false)}</div>`); draw.push(()=>lineSmart(id1,[item.series.find(s=>/_close$/.test(s.id)),item.series.find(s=>/_ret_20d$/.test(s.id))].filter(Boolean),{max:160,rebase:false}),()=>lineSmart(id2,[item.series.find(s=>/_vol_20d$/.test(s.id)),item.series.find(s=>/_drawdown_60d$/.test(s.id))].filter(Boolean),{max:160})); }
    html.push(tableHTML('\u5df2\u9009\u7533\u4e07\u884c\u4e1a',sel,['code','industry','close','ret_1d','ret_5d','ret_20d','vol_20d','mdd_60d','as_of'])); root(html.join('')); $('sw-apply').onclick=async()=>{S.sw=Array.from($('sw-select').selectedOptions).map(x=>x.value); if(!S.sw.length)S.sw=null; await sw();}; $('sw-reset').onclick=async()=>{S.sw=null; await sw();}; draw.forEach(f=>f()); }


  function control(id,label,rows,key,selected,applyId,resetId){
    const chosen = new Set(arr(selected).map(String));
    const opts = arr(rows).map(r=>{
      const v = String(obj(r)[key] ?? "");
      if(!v) return "";
      const extra = key==='industry' && r.code ? ` (${esc(r.code)})` : "";
      return `<option value="${esc(v)}" ${chosen.has(v)?'selected':''}>${esc(v)}${extra}</option>`;
    }).join("");
    return `<section class="control-card"><div class="control-grid"><label style="grid-column:span 3;">${esc(label)}<select id="${esc(id)}" multiple size="8">${opts}</select></label><button id="${esc(applyId)}" class="action-button" type="button">${TXT.update}</button><button id="${esc(resetId)}" class="ghost-button" type="button">${TXT.coreGroup}</button></div></section>`;
  }

  async function cmdty(){ const rows=arr(table('commodities','commodity_market_matrix').rows), basis=arr(table('commodities','commodity_basis_snapshot').rows); if(!S.cmdty) S.cmdty=pick(rows,['AU','AG','CU','AL','RB','I','SC','TA'],'symbol',8); const sel=rows.filter(r=>S.cmdty.includes(r.symbol)); const asof=maxDate(sel.length?sel:rows); const best=[...rows].sort((a,b)=>+b.ret_20d-+a.ret_20d)[0]||{}, worst=[...rows].sort((a,b)=>+a.ret_20d-+b.ret_20d)[0]||{}; conclusion(`\u622a\u81f3 ${asof}, \u5927\u5b97\u5546\u54c120\u65e5\u6700\u5f3a ${esc(best.symbol)} ${signed(best.ret_20d)}%, \u6700\u5f31 ${esc(worst.symbol)} ${signed(worst.ret_20d)}%; \u5df2\u9009 ${sel.length} \u4e2a\u671f\u8d27\u54c1\u79cd.`); const p1=pid('c'),p2=pid('c'),p3=pid('c'),p4=pid('c'); const close=await fetchSeries(sel.map(r=>`commodity_${String(r.symbol).toLowerCase()}_main_close`)); const ship=await fetchSeries(['cmdty_bdi','cmdty_bci','cmdty_bpi','cmdty_bcti','cmdty_bdti']); root(control('cmdty-select',TXT.cmdtyPick,rows,'symbol',S.cmdty,'cmdty-apply','cmdty-reset')+cardHTML([{label:'\u54c1\u79cd\u6570\u91cf',value:rows.length},{label:'20\u65e5\u6700\u5f3a',value:best.ret_20d,unit:'%',as_of:best.symbol},{label:'20\u65e5\u6700\u5f31',value:worst.ret_20d,unit:'%',as_of:worst.symbol},{label:'\u57fa\u5dee\u6837\u672c',value:basis.length}])+`<div class="panel-grid">${panel(p1,'\u5df2\u9009\u671f\u8d27\u8d70\u52bf','\u57fa\u671f=100',true)}${panel(p2,'20\u65e5\u6536\u76ca','',false)}${panel(p3,'\u98ce\u9669\u6536\u76ca','\u6a2a\u8f7420\u65e5\u6ce2\u52a8\uff0c\u7eb5\u8f7420\u65e5\u6536\u76ca',false)}${panel(p4,'\u822a\u8fd0\u6307\u6570','BDI/BCI/BPI/BCTI/BDTI',false)}</div>`+tableHTML('\u5df2\u9009\u5927\u5b97\u5546\u54c1',sel,['symbol','close','ret_1d','ret_20d','vol_20d','mdd_60d','as_of'])+tableHTML('\u57fa\u5dee\u5feb\u7167',basis.filter(r=>S.cmdty.includes(r.symbol)),['symbol','spot','near_contract','near_price','dominant_contract','dominant_price','near_basis','dominant_basis','near_basis_rate','dominant_basis_rate'])); $('cmdty-apply').onclick=async()=>{S.cmdty=Array.from($('cmdty-select').selectedOptions).map(x=>x.value); if(!S.cmdty.length)S.cmdty=null; await cmdty();}; $('cmdty-reset').onclick=async()=>{S.cmdty=null; await cmdty();}; line(p1,close,{rebase:true,max:180}); bar(p2,sel.map(r=>({label:r.symbol,value:r.ret_20d}))); scatter(p3,sel.map(r=>({label:r.symbol,x:r.vol_20d,y:r.ret_20d}))); lineSmart(p4,ship,{max:240}); }

  function relatedNews(rows,code,name){ const d=digits(code); return arr(rows).filter(r=>digits(r.code)===d||String(r.title||'').includes(name||'')||String(r.title||'').includes(d)).sort((a,b)=>String(b.published_at).localeCompare(String(a.published_at))); }
  async function stock(){ const base=S.stockOverride||mod('stock'), rows=arr(table('stock','stock_watchlist').rows); if(!S.stockCode)S.stockCode=(rows[0]&&rows[0].code)||'000001'; const activeRows=arr((arr(base.tables).find(x=>x.id==='stock_watchlist')||{}).rows||rows), r=activeRows[0]||rows[0]||{}; const newsRows=relatedNews(arr(table('news_events','news_feed').rows),r.code||S.stockCode,r.name); const asof=r.as_of||maxDate(activeRows); conclusion(`\u622a\u81f3 ${asof}, ${esc(r.name||S.stockCode)} \u65e5\u6da8\u8dcc ${signed(r.ret_1d)}%, \u5468\u6da8\u8dcc ${signed(r.ret_5d)}%, 20\u65e5\u6da8\u8dcc ${signed(r.ret_20d)}%, 20\u65e5\u56de\u64a4 ${signed(r.mdd_20d)}%; \u76f8\u5173\u65b0\u95fb ${newsRows.length}, \u6700\u65b0 ${esc((newsRows[0]||{}).published_at||'--')}.`); const p1=pid('s'),p2=pid('s'); const stockDigits=digits(r.code||S.stockCode); let stockMeta=arr(base.series).filter(s=>/close|ret_5d|ret_20d|mdd_20d|vol_20d/.test(s.id)); const scopedMeta=stockDigits?stockMeta.filter(s=>digits((s.id||'')+' '+(s.label||'')).includes(stockDigits)||String(s.label||'').includes(r.name||'')):stockMeta; if(scopedMeta.length) stockMeta=scopedMeta; const ss=await hydrate(stockMeta); root(`<section class="control-card"><div class="control-grid"><label>${TXT.stockPick}<select id="stock-preset">${rows.map(x=>`<option value="${esc(x.code)}" ${String(x.code)===String(S.stockCode)?'selected':''}>${esc(cnText(x.code))} ${esc(x.name)}</option>`).join('')}</select></label><label style="grid-column:span 2;">${TXT.inputCode}<input id="stock-input" value="${esc(S.stockCode)}"></label><button id="stock-load" class="action-button" type="button">${TXT.loadStock}</button><button id="stock-ai" class="ghost-button" type="button">AI\u667a\u80fd\u5206\u6790</button></div></section>`+cardHTML([{label:'\u6536\u76d8\u4ef7',value:r.close??r.qfq_close,unit:'\u5143',as_of:asof},{label:'1\u5468\u6da8\u8dcc',value:r.ret_5d,unit:'%'},{label:'20\u65e5\u6da8\u8dcc',value:r.ret_20d,unit:'%'},{label:'20\u65e5\u56de\u64a4',value:r.mdd_20d,unit:'%'}])+`<div id="stock-ai-result" class="ai-panel is-compact"><p>\u70b9\u51fbAI\u667a\u80fd\u5206\u6790\uff0c\u751f\u6210\u8d8b\u52bf\u3001\u98ce\u9669\u4e0e\u65b0\u95fb\u5f71\u54cd\u6bb5\u843d\u3002</p></div><div class="panel-grid">${panel(p1,'\u4e2a\u80a1\u8d70\u52bf\u4e0e\u6536\u76ca','\u5de6\u8f74\u4ef7\u683c\uff0c\u53f3\u8f74\u6536\u76ca/\u56de\u64a4',true)}${panel(p2,'\u81ea\u9009\u80a1\u98ce\u9669\u6536\u76ca','\u6a2a\u8f7420\u65e5\u6ce2\u52a8\uff0c\u7eb5\u8f7420\u65e5\u6536\u76ca',false)}</div><section class="chart-panel wide"><div class="panel-header"><div><h3>\u4e2a\u80a1\u65b0\u95fb\u6eda\u52a8</h3><p>\u6309\u4ee3\u7801/\u540d\u79f0\u5339\u914d\uff0c\u6700\u65b0\u4f18\u5148\uff1b\u60ac\u505c\u6682\u505c</p></div></div><div class="news-ticker stock-news"><div class="news-list">${(newsRows.length?newsRows:arr(table('news_events','news_feed').rows).slice(0,8)).concat(newsRows).map(newsItem).join('')}</div></div></section>`+tableHTML('\u4e2a\u80a1\u884c\u60c5',activeRows,['code','name','close','qfq_close','ret_1d','ret_5d','ret_20d','vol_20d','mdd_20d','turnover','as_of'])+tableHTML('\u76f8\u5173\u4e2a\u80a1\u65b0\u95fb',newsRows,['published_at','event_type','code','title','source','url'])); $('stock-preset').onchange=()=>{$('stock-input').value=$('stock-preset').value;}; $('stock-load').onclick=async()=>{const c=$('stock-input').value.trim(); if(!c)return; S.stockCode=c; try{const p=await api('/api/board/stock/'+encodeURIComponent(c)); S.stockOverride=p.data||null;}catch(e){S.stockOverride=null; conclusion('\u4e2a\u80a1\u52a0\u8f7d\u5931\u8d25\uff1a'+esc(e.message));} await stock();}; $('stock-ai').onclick=()=>aiFill('stock',`${r.code||S.stockCode} ${r.name||''}`,{quote:r,news:newsRows.slice(0,12),series:ss.map(x=>({id:x.id,label:x.label,latest:latest(x),as_of:x.as_of}))},'stock-ai-result'); lineSmart(p1,ss,{max:180}); scatter(p2,rows.map(x=>({label:x.name||x.code,x:x.vol_20d,y:x.ret_20d}))); }

  function industryHeat(rows){ const buckets=[['\u7535\u5b50/\u534a\u5bfc\u4f53',['AI','chip','semiconductor','300750','\u82af\u7247','\u534a\u5bfc\u4f53','\u7535\u5b50']],['\u94f6\u884c\u91d1\u878d',['000001','bank','finance','\u94f6\u884c','\u91d1\u878d']],['\u98df\u54c1\u996e\u6599',['600519','baijiu','consumer','\u767d\u9152','\u6d88\u8d39','\u98df\u54c1']],['\u7535\u529b\u8bbe\u5907/\u65b0\u80fd\u6e90',['battery','lithium','new energy','300750','\u7535\u6c60','\u9502','\u65b0\u80fd\u6e90']],['\u8ba1\u7b97\u673a/AI',['software','cloud','AI','\u8f6f\u4ef6','\u4e91','\u7b97\u529b']],['\u533b\u836f\u751f\u7269',['drug','medical','\u533b\u836f','\u533b\u7597','\u836f']],['\u6709\u8272\u91d1\u5c5e',['copper','gold','aluminum','\u94dc','\u91d1','\u94dd','\u6709\u8272']],['\u6c7d\u8f66',['auto','vehicle','\u6c7d\u8f66','\u6574\u8f66']]]; const out=buckets.map(([label])=>({label,value:0})); arr(rows).forEach(r=>{ const t=(String(r.title||'')+' '+String(r.code||'')).toLowerCase(); buckets.forEach(([label,keys],i)=>{ if(keys.some(k=>t.includes(String(k).toLowerCase()))) out[i].value+=2; }); }); return out.filter(x=>x.value>0).sort((a,b)=>b.value-a.value).slice(0,12); }
  function stockHeat(rows){ const names={'000001':'\u5e73\u5b89\u94f6\u884c','600519':'\u8d35\u5dde\u8305\u53f0','300750':'\u5b81\u5fb7\u65f6\u4ee3'}; const map={}; arr(rows).forEach(r=>{ const raw=String(r.code||'').trim(); const key=raw?(names[raw]||raw):'\u5e02\u573a\u7efc\u5408'; map[key]=(map[key]||0)+1; }); return Object.entries(map).map(([label,value])=>({label,value})).sort((a,b)=>b.value-a.value).slice(0,10); }
  async function news(){ const rows=arr(table('news_events','news_feed').rows).sort((a,b)=>String(b.published_at).localeCompare(String(a.published_at))); const latest=rows[0]||{}; const latestDay=(latest.published_at||'').slice(0,10); const oneWeek=rows.filter(r=>!latestDay||String(r.published_at||'').slice(0,10)>=latestDay.replace(/-(\d\d)$/,(m,d)=>'-'+String(Math.max(1,Number(d)-7)).padStart(2,'0'))); const title=String(latest.title||''); const lower=title.toLowerCase(); const impact=['down','risk','loss','sell','\u98ce\u9669','\u4e0b\u8dcc','\u51cf\u6301'].some(k=>lower.includes(k))?'\u98ce\u9669\u6270\u52a8':['up','buy','growth','profit','\u589e\u957f','\u5229\u597d','\u4e70\u5165'].some(k=>lower.includes(k))?'\u6b63\u5411\u50ac\u5316':'\u4fe1\u606f\u50ac\u5316'; conclusion(`\u622a\u81f3 ${esc(latest.published_at||'--')}\uff0c\u6700\u65b0\u4e8b\u4ef6\uff1a\u201c${esc(latest.title||'\u6682\u65e0\u6807\u9898')}\u201d\uff0c\u6765\u6e90 ${esc(latest.source||'--')}\uff1b\u521d\u6b65\u5f71\u54cd\u5224\u65ad\u4e3a${impact}\u3002\u6700\u8fd1\u4e00\u5468\u7eb3\u5165 ${oneWeek.length||rows.length} \u6761\u4e8b\u4ef6\uff0c\u91cd\u70b9\u89c2\u5bdf\u884c\u4e1a\u4e0e\u4e2a\u80a1\u70ed\u5ea6\u96c6\u4e2d\u5ea6\u3002`); const p1=pid('n'),p2=pid('n'); const ih=industryHeat(oneWeek.length?oneWeek:rows), sh=stockHeat(oneWeek.length?oneWeek:rows); root(cardHTML([{label:'\u8fd1\u4e00\u5468\u65b0\u95fb',value:oneWeek.length||rows.length,unit:'',as_of:latestDay},{label:'\u884c\u4e1a\u70ed\u5ea6\u5206\u7ec4',value:ih.length,unit:''},{label:'\u4e2a\u80a1\u70ed\u5ea6\u5206\u7ec4',value:sh.length,unit:''},{label:'\u6700\u65b0\u6765\u6e90',value:rows.length,as_of:latest.source}])+`<div class="panel-grid"><section class="chart-panel wide"><div class="panel-header"><div><h3>\u65b0\u95fb\u6eda\u52a8</h3><p>\u6700\u8fd1\u4e00\u5468\uff0c\u6700\u65b0\u4f18\u5148\uff1b\u60ac\u505c\u6682\u505c\u6eda\u52a8</p></div></div><div class="news-ticker"><div class="news-list">${(oneWeek.length?oneWeek:rows).concat(oneWeek.length?oneWeek:rows).map(newsItem).join('')}</div></div></section>${panel(p1,'\u884c\u4e1a\u9f99\u864e\u699c\u70ed\u5ea6\u6392\u540d','\u6700\u8fd1\u4e00\u5468\u5173\u952e\u8bcd/\u4e8b\u4ef6\u8ba1\u6570',false)}${panel(p2,'\u4e2a\u80a1\u9f99\u864e\u699c\u70ed\u5ea6TOP10','\u6700\u8fd1\u4e00\u5468\u4e8b\u4ef6\u8ba1\u6570',false)}</div>`+tableHTML('\u65b0\u95fb\u660e\u7ec6',oneWeek.length?oneWeek:rows,['published_at','event_type','code','title','source','url'])); bar(p1,ih); bar(p2,sh); }
  function newsItem(r){ return `<a class="news-item" href="${esc(r.url||'#')}" target="_blank" rel="noreferrer"><time>${esc(r.published_at||'')}</time><strong>${esc(r.title||'')}</strong><small>${esc(r.source||r.event_type||'')}</small></a>`; } function count(rows,key){ return rows.reduce((a,r)=>{const k=r[key]||'NA'; a[k]=(a[k]||0)+1; return a;},{}); }

  async function renderKline(v){ const h=HEAD['kline:'+v]||HEAD['kline:home']; header(h[0],h[1],'K\u7ebf\u8bb0\u5fc6\u5b66\u4e60'); $('as-of').textContent='\u670d\u52a1'; $('generated-at').textContent='\u6309\u9700\u751f\u6210'; if(v==='home')return await klineHome(); if(v==='learn')return await klineLearn(); if(v==='backtest')return await klineBacktest(); if(v==='history')return await klineHistory(); }
  async function needKline(){ try{ const tasks=[];if(!S.kline.health)tasks.push(api('/api/kline/health').then(function(value){S.kline.health=value;}));if(!S.kline.history.length)tasks.push(api('/api/kline/history?limit=80').then(function(value){S.kline.history=arr(value.history);}));await Promise.all(tasks); }catch(e){conclusion('K\u7ebf\u670d\u52a1\u6682\u4e0d\u53ef\u7528\uff1a'+esc(e.message));} }
  function currentKlineJob(){ return S.kline.job||S.kline.selectedJob||S.kline.history[0]||{}; }
  async function loadKlineJob(id){ if(!id)return null; const j=await api('/api/kline/jobs/'+encodeURIComponent(id)); S.kline.job=j; S.kline.selectedJob=j; return j; }
  function klineControls(){ return `<section class="control-card"><div class="control-grid"><label style="grid-column:span 2;">\u80a1\u7968\u641c\u7d22<input id="kq" value="000001"></label><button id="ks" class="ghost-button" type="button">${TXT.search}</button><label style="grid-column:span 2;">\u80a1\u7968<select id="kst"></select></label><label>\u622a\u6b62\u65e5\u671f<select id="kd"><option value="latest">\u6700\u65b0\u53ef\u7528</option></select></label><label>\u5206\u6790\u6df1\u5ea6<select id="kdepth"><option value="fast">\u5feb\u901f\uff1a\u672c\u5730\u8bb0\u5fc6</option><option value="standard">\u6807\u51c6\uff1aGPT\u590d\u6838</option><option value="deep">\u6df1\u5ea6\uff1a\u89c4\u5219\u6539\u5199</option></select></label><label>\u6301\u6709\u7a97\u53e3<select id="kh"><option value="20">20\u65e5</option><option value="10">10\u65e5</option><option value="30">30\u65e5</option><option value="60">60\u65e5</option></select></label><label>\u4ed3\u4f4d\u6863\u4f4d<select id="kp"><option value="balanced">\u5e73\u8861 30/50/100</option><option value="conservative">\u4fdd\u5b88 20/40/80</option><option value="aggressive">\u79ef\u6781 50/80/100</option></select></label><button id="kstart" class="action-button" type="button">\u5f00\u59cb\u5b66\u4e60</button></div></section>`; }
  async function bindKlineControls(){ $('ks').onclick=()=>kSearch($('kq').value.trim()); $('kst').onchange=()=>kDates($('kst').value); $('kstart').onclick=kStart; await kSearch('000001'); }
  async function klineHome(){ await needKline(); const h=S.kline.health||{}, j=currentKlineJob(); clearConclusion(); root(klineControls()+cardHTML([{label:'\u8fd0\u884c\u4e2d',value:h.running_jobs||0},{label:'\u5386\u53f2\u4efb\u52a1',value:S.kline.history.length},{label:'GPT',value:h.gpt_configured?1:0,unit:h.gpt_model||''},{label:'\u4efb\u52a1\u603b\u6570',value:h.job_count||0}])+`<section id="kpanel" class="workbench-panel"><h2>\u5f53\u524d\u4efb\u52a1</h2>${jobHTML(j,'kline')}</section>`); await bindKlineControls(); }
  async function kSearch(q){ const p=await api('/api/kline/stocks?limit=80&q='+encodeURIComponent(q||'')); S.kline.stocks=arr(p.stocks); if($('kst')) $('kst').innerHTML=S.kline.stocks.map(s=>`<option value="${esc(s.code)}">${esc(cnText(s.code))} ${esc(s.name||'')}</option>`).join(''); if(S.kline.stocks[0]) await kDates(S.kline.stocks[0].code); }
  async function kDates(c){ const p=await api('/api/kline/dates?code='+encodeURIComponent(c)); S.kline.dates=arr(p.dates); if($('kd')) $('kd').innerHTML='<option value="latest">\u6700\u65b0\u53ef\u7528\u65e5\u671f</option>'+S.kline.dates.slice(0,150).map(d=>`<option value="${esc(d)}">${esc(d)}</option>`).join(''); }
  async function kStart(){ const payload={code:$('kst').value,as_of:$('kd').value,analysis_depth:$('kdepth').value,holding_days:$('kh').value,position_profile:$('kp').value,cohort_mode:$('kcohort')?$('kcohort').value:'hybrid'}; const j=await api('/api/kline/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}); S.kline.job=j; if($('kpanel'))$('kpanel').innerHTML='<h2>\u5f53\u524d\u4efb\u52a1</h2>'+jobHTML(j,'kline'); await kPoll(j.job_id); }
  async function kPoll(id){ for(let i=0;i<180&&id;i++){ const j=await api('/api/kline/jobs/'+encodeURIComponent(id)+'?live=1&ts='+Date.now()); S.kline.job=j; S.kline.selectedJob=j; if($('kpanel'))$('kpanel').innerHTML='<h2>\u5f53\u524d\u4efb\u52a1</h2>'+jobHTML(j,'kline'); if(!['queued','running'].includes(String(j.status)))return; await sleep(3000);} }
  function kMemoryRows(j){ const sum=obj(j.summary), cats=obj(sum.learning_categories); let rows=[]; Object.keys(cats).forEach(cat=>arr(cats[cat]).forEach(r=>rows.push(Object.assign({category:cat},r)))); return rows.sort((a,b)=>(Number(b.confidence||0)-Number(a.confidence||0))).slice(0,36); }
  function statusText(v){ return valueText(v||'\u53ef\u7528').replace(/active/ig,'\u6709\u6548').replace(/available/ig,'\u53ef\u7528'); }
  function freqText(v){ return String(v||'--').replace(/(\d+)D\b/ig,'$1\u65e5').replace(/(\d+)W\b/ig,'$1\u5468'); }
  function ruleNote(r){ let s=String(r.note_text||r.explanation||'\u89e6\u53d1\u6761\u4ef6\u3001\u8bc1\u636e\u4e0e\u98ce\u9669\u7ea6\u675f\u53ef\u5728\u89c4\u5219\u660e\u7ec6\u4e2d\u67e5\u770b\u3002'); s=s.replace(/\[[A-Z0-9_]{3,}\]\s*/g,'').replace(/\bactive\b/ig,'\u6709\u6548').replace(/\bavailable\b/ig,'\u53ef\u7528').replace(/\bfailed\b/ig,'\u5931\u8d25').replace(/\bdone\b/ig,'\u5b8c\u6210'); return s; }
  function memoryCards(rows){ if(!rows.length)return '<div class="empty-state">\u6682\u65e0\u8bb0\u5fc6\u8bb0\u5f55\u3002</div>'; return `<div class="memory-list">${rows.map(r=>`<article class="memory-row"><span class="pill ${esc(r.status||'')}">${esc(statusText(r.status||'available'))}</span><strong>${esc(freqText(r.frequency||'--'))} | ${esc(r.name_cn||r.rule_id||'')}</strong><em>${fmt((r.confidence||0)*100,0)}%</em><p>${esc(ruleNote(r))}</p></article>`).join('')}</div>`; }
  async function klineLearn(){
    await needKline();
    let j=currentKlineJob();
    if(j.job_id&&!j.summary) j=await loadKlineJob(j.job_id);
    const rows=kMemoryRows(j), empty=!j.job_id;
    clearConclusion();
    root((empty?klineControls():'')+`<section class="workbench-panel"><h2>记忆层</h2>${empty?'<p class="empty-state">尚无学习任务。请在上方选择股票、日期、分析深度与持有窗口，然后点击“开始学习”。</p>':''}${jobHTML(j,'kline')}${memoryCards(rows)}</section>`);
    if(empty) await bindKlineControls();
  }
  function klineCandle(id,j){ const sum=obj(j.summary), daily=arr(obj(sum.chart_data).daily).slice(-900), nodes=arr(sum.signal_nodes); const x=daily.map(r=>String(r[0]).replace(/(\d{4})(\d{2})(\d{2})/,'$1-$2-$3')); const trace={type:'candlestick',x,open:daily.map(r=>+r[1]),high:daily.map(r=>+r[2]),low:daily.map(r=>+r[3]),close:daily.map(r=>+r[4]),name:'K\u7ebf',increasing:{line:{color:'#c00000'}},decreasing:{line:{color:'#168a47'}}}; const vol={type:'bar',x,y:daily.map(r=>+r[6]),name:'\u6210\u4ea4\u91cf',yaxis:'y2',marker:{color:'rgba(47,117,181,.25)'}}; const buys=nodes.filter(n=>/buy|hold/.test(String(n.action))); const sells=nodes.filter(n=>/sell|reduce/.test(String(n.action))); const mk=(arrs,name,color,sym)=>({type:'scatter',mode:'markers',name,x:arrs.map(n=>String(n.execution_date||n.date).replace(/(\d{4})(\d{2})(\d{2})/,'$1-$2-$3')),y:arrs.map(n=>n.execution_price||n.price),marker:{symbol:sym,size:9,color}}); plot(id,daily.length?[trace,vol,mk(buys,'\u4e70\u5165/\u6301\u6709','#168a47','triangle-up'),mk(sells,'\u5356\u51fa/\u51cf\u4ed3','#c46a08','triangle-down')]:[],{xaxis:{rangeslider:{visible:false},showgrid:false},yaxis:{domain:[.24,1],gridcolor:'#edf0f2'},yaxis2:{domain:[0,.16],showgrid:false},legend:{orientation:'h',y:-.18,font:{size:10}},hovermode:'x unified',margin:{l:44,r:18,t:12,b:44}}); }
  function klineEquity(id,j){ const eq=arr(obj(obj(j.summary).backtest_panel).equity); const x=eq.map(r=>String(r[0]).replace(/(\d{4})(\d{2})(\d{2})/,'$1-$2-$3')); plot(id,eq.length?[{type:'scatter',mode:'lines',name:'\u7b56\u7565\u51c0\u503c',x,y:eq.map(r=>+r[1]),line:{color:'#3f6f5f',width:2}},{type:'scatter',mode:'lines',name:'\u4e70\u5165\u6301\u6709',x,y:eq.map(r=>+r[5]),line:{color:'#98a2b3',width:2}}]:[],{hovermode:'x unified',legend:{orientation:'h',y:-.18},yaxis:{gridcolor:'#edf0f2'},xaxis:{showgrid:false}}); }
  async function klineBacktest(){ await needKline(); let j=currentKlineJob(); if(j.job_id&&!j.summary) j=await loadKlineJob(j.job_id); const c1=pid('kc'),c2=pid('ke'), met=obj(obj(j.summary).backtest_metrics||obj(obj(j.summary).backtest_panel).metrics); const metricsRows=Object.entries(met).map(([set,m])=>Object.assign({set},obj(m))); conclusion(`\u56de\u6d4b\u4efb\u52a1 ${esc(j.job_id||'--')}\uff1a\u5168\u6837\u672c\u6536\u76ca ${fmt((obj(met.full).total_return||0)*100,1)}%, \u6d4b\u8bd5\u6536\u76ca ${fmt((obj(met.test).total_return||0)*100,1)}%, \u6700\u5927\u56de\u64a4 ${fmt((obj(met.full).max_drawdown||0)*100,1)}%.`); root(`<div class="panel-grid full">${panel(c1,'\u4fe1\u53f7K\u7ebf\u56fe','\u4ef7\u683c\u3001\u6210\u4ea4\u91cf\u4e0e\u4ea4\u6613\u4fe1\u53f7',true)}${panel(c2,'\u56de\u6d4b\u51c0\u503c','\u7b56\u7565\u5bf9\u6bd4\u4e70\u5165\u6301\u6709',true)}</div>`+tableHTML('\u56de\u6d4b\u6307\u6807',metricsRows,['set','total_return','annual_return','max_drawdown','sharpe','calmar','avg_position','signal_trigger_count','buy_hold_return'])); klineCandle(c1,j); klineEquity(c2,j); }
  async function klineHistory(){ await needKline(); clearConclusion(); const rows=S.kline.history.map(r=>Object.assign({view:'\u67e5\u770b'},r)); root(tableHTML('K\u7ebf\u5386\u53f2\u8bb0\u5f55',rows,['created_at','code','as_of','status','analysis_depth','holding_days','test_return','max_drawdown','view','job_id'])); document.querySelectorAll('[data-kline-view]').forEach(a=>a.onclick=async(e)=>{e.preventDefault(); await loadKlineJob(a.dataset.klineView); S.active='kline:learn'; document.querySelectorAll('.nav-item').forEach(x=>x.classList.toggle('is-active',x.dataset.target===S.active)); await render();}); }

  async function renderFactor(v){ const h=HEAD['factor:'+v]||HEAD['factor:home']; header(h[0],h[1],'LLM\u56e0\u5b50\u6316\u6398'); $('as-of').textContent='\u670d\u52a1'; $('generated-at').textContent='\u6309\u9700\u751f\u6210'; if(v==='home')return await factorHome(); if(v==='memory')return await factorMemory(); await factorDetail(); if(v==='expression')return factorExpression(); if(v==='report')return factorReport(); if(v==='score')return factorScore(); }
  async function needFactor(){ try{ if(!S.factor.status)S.factor.status=await api('/api/factor/status'); if(!S.factor.history)S.factor.history=await api('/api/factor/history'); }catch(e){conclusion('\u56e0\u5b50\u670d\u52a1\u6682\u4e0d\u53ef\u7528\uff1a'+esc(e.message));} }
  function fRows(){ const h=S.factor.history||{}; return arr(h.account_history).concat(arr(h.server_runs)); }
  async function factorDetail(jobId){ await needFactor(); if(jobId){S.factor.detail=await api('/api/factor/history/'+encodeURIComponent(jobId)); S.factor.selectedJob=jobId; return;} if(S.factor.detail)return; const rows=fRows(); if(rows[0]&&rows[0].job_id){ try{S.factor.detail=await api('/api/factor/history/'+encodeURIComponent(rows[0].job_id)); S.factor.selectedJob=rows[0].job_id;}catch(_){S.factor.detail=null;} } }
  function fResult(){ return obj((S.factor.detail||{}).result||(S.factor.job||{}).result); } function reports(){ const r=fResult(); return arr(r.factor_reports||r.accepted_factors||r.leaderboard); }
  function selectedFactor(){ const rs=reports(); const i=Math.max(0,Math.min(Number(S.factor.selectedIndex||0),rs.length-1)); return rs[i]||{}; }
  async function factorHome(){ await needFactor(); await factorDetail(); const st=S.factor.status||{}, rows=fRows(), rs=reports(); clearConclusion(); root(cardHTML([{label:'GPT',value:st.ai_router_configured?1:0,unit:st.model||''},{label:'\u5019\u9009\u56e0\u5b50',value:rs.length},{label:'\u5386\u53f2\u4efb\u52a1',value:rows.length},{label:'\u5f53\u524d\u4efb\u52a1',value:S.factor.selectedJob||'--'}])+`<section class="control-card"><div class="control-grid"><label style="grid-column:span 2;">\u56e0\u5b50<select id="fsel">${rs.map((r,i)=>`<option value="${i}" ${i===(S.factor.selectedIndex||0)?'selected':''}>${i+1}. ${esc(r.chinese_name||r.name||r.factor||'\u56e0\u5b50')}</option>`).join('')}</select></label><label>\u80a1\u7968\u6c60<select id="fu"><option>ALL_A</option><option>CSI800_ENH</option><option>CSI2000_ENH</option></select></label><label>\u6708\u6570<input id="fm" value="full"></label><label>\u8fed\u4ee3\u8f6e\u6570<input id="fi" type="number" min="1" max="8" value="6"></label><label>\u76ee\u6807\u6570\u91cf<input id="ft" type="number" min="1" max="20" value="1"></label><button id="fstart" class="action-button" type="button">${TXT.start}</button></div></section><section id="fpanel" class="workbench-panel"><h2>\u5f53\u524d\u4efb\u52a1</h2>${jobHTML(S.factor.job||S.factor.detail||{},'factor')}</section>`); if($('fsel'))$('fsel').onchange=async()=>{S.factor.selectedIndex=Number($('fsel').value||0); await render();}; $('fstart').onclick=fStart; }
  async function fStart(){ const payload={universe:$('fu').value,max_months:$('fm').value,iterations:$('fi').value,target_accepted:$('ft').value,budget_per_channel:6,max_candidates:20}; const j=await api('/api/factor/job/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}); S.factor.job=j; await fPoll(j.id); }
  async function fPoll(id){ for(let i=0;i<240&&id;i++){ const j=await api('/api/factor/job/'+encodeURIComponent(id)); S.factor.job=j; if($('fpanel'))$('fpanel').innerHTML='<h2>\u5f53\u524d\u4efb\u52a1</h2>'+jobHTML(j,'factor'); if(!['queued','running'].includes(String(j.status))){S.factor.detail={result:j.result,job_id:id}; return;} await sleep(5000);} }
  function factorVarLogic(){ return {op_yoy:['营业收入同比','行业内排序后取两期差分','经营动量与景气改善'],large_order_balance:['大单资金净额','行业内排序后取三期均值','资金流持续性'],pb:['市净率','行业内负向排序','估值安全边际'],base_low_crowding:['低拥挤度','行业内排序','避免拥挤交易'],base_event_risk:['事件风险','行业内负向排序','降低负面事件暴露'],turnover:['换手率','行业内排序','流动性活跃度'],op_qoq:['营业收入环比','行业内排序','短期经营改善']}; }
  function varsOf(formula){ const raw=String(formula||'').replace(/\\_/g,'_'); const logic=factorVarLogic(); return Object.keys(logic).filter(k=>raw.includes(k)).slice(0,8); }
  function variableRows(formula){ const logic=factorVarLogic(); return varsOf(formula).map((v,i)=>{ const m=logic[v]; return {proxy:'X'+(i+1),variable:v,name:m[0],formula:m[1],logic:m[2]}; }); }
  function replaceToken(src, token, proxy){ const escToken=token.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'); return src.replace(new RegExp('(^|[^A-Za-z0-9_])'+escToken+'(?![A-Za-z0-9_])','g'), '$1'+proxy); }
  function formulaHTML(raw){ let x=String(raw||'').replace(/\\_/g,'_'); const vars=variableRows(raw); vars.forEach(v=>{ x=replaceToken(x,v.variable,v.proxy); }); x=x.replace(/\\operatorname\{([^}]+)\}/g,'$1').replace(/\\mathrm\{([^}]+)\}/g,'$1').replace(/\\left|\\right/g,'').replace(/\\cdot/g,'·').replace(/\\Delta/g,'Δ').replace(/\\sigma/g,'σ').replace(/[{}]/g,''); x=esc(x); x=x.replace(/_\{?([0-9A-Za-z]+)\}?/g,'<sub>$1</sub>'); return x; }
  function factorExpression(){ const rs=reports(), x=selectedFactor(), formula=x.latex_formula||x.formula||x.expression||''; const vars=variableRows(formula); conclusion('当前因子 '+esc(x.chinese_name||x.name||'--')+'，状态 '+esc(fmStatus(x.status||x.lifecycle_state))+'，部署置信度 '+fmPct(x.lifecycle_deployment_confidence)+'。'); root(`<section class="workbench-panel"><h2>${esc(x.chinese_name||x.name||x.factor||'\u6682\u65e0\u56e0\u5b50')}</h2><div class="pill-row">${pill('status',x.status||x.lifecycle_state)}${pill('confidence',x.lifecycle_deployment_confidence)}${pill('complexity',x.complexity)}${pill('channel',x.channel)}</div><div class="formula-box rendered-formula">${formulaHTML(formula)||'\u6682\u65e0\u516c\u5f0f'}</div><details class="source-box"><summary>\u67e5\u770bLaTeX\u6e90\u7801</summary><pre>${esc(formula)}</pre></details></section>`+tableHTML('\u4ee3\u7406\u53d8\u91cf\u8868',vars,['proxy','name','formula','logic'])+tableHTML('\u5019\u9009\u56e0\u5b50\u6c60',rs,['chinese_name','name','status','channel','test_rank_ic','rank_ic','lifecycle_deployment_confidence','redundancy_max_abs_corr'])); }
  function factorCurve(id,x){ const curve=arr(x.backtest_curve||x.search_backtest_curve); plot(id,curve.length?[{type:'scatter',mode:'lines',name:'\u591a\u7a7a\u51c0\u503c',x:curve.map(r=>r.date),y:curve.map(r=>r.long_short_nav),line:{color:'#2f75b5',width:2}},{type:'scatter',mode:'lines',name:'\u591a\u5934\u51c0\u503c',x:curve.map(r=>r.date),y:curve.map(r=>r.long_nav),line:{color:'#168a47',width:2}},{type:'scatter',mode:'lines',name:'\u57fa\u51c6\u51c0\u503c',x:curve.map(r=>r.date),y:curve.map(r=>r.benchmark_nav),line:{color:'#98a2b3',width:2}}]:[],{hovermode:'x unified',legend:{orientation:'h',y:-.18},yaxis:{gridcolor:'#edf0f2'},xaxis:{showgrid:false}}); }
  function factorIC(id,x){ const ic=arr(x.ic_series); plot(id,ic.length?[{type:'bar',name:'RankIC',x:ic.map(r=>r.date),y:ic.map(r=>r.rank_ic),marker:{color:ic.map(r=>Number(r.rank_ic)>=0?'#168a47':'#c00000')}},{type:'scatter',mode:'lines',name:'\u5206\u7ec4\u6536\u76ca\u5dee',x:ic.map(r=>r.date),y:ic.map(r=>r.group_spread),yaxis:'y2',line:{color:'#2f75b5',width:2}}]:[],{hovermode:'x unified',legend:{orientation:'h',y:-.18},yaxis:{title:'RankIC',gridcolor:'#edf0f2'},yaxis2:{title:'\u6536\u76ca\u5dee',overlaying:'y',side:'right',showgrid:false},xaxis:{showgrid:false}}); }
  function factorReport(){ const x=selectedFactor(), wf=obj(x.walk_forward||x.walkforward||x.validation||{}), wins=arr(wf.windows||wf.folds), c1=pid('fc'), c2=pid('fi'); const positive=(wf.positive_rate ?? wf.positive_test_ic_ratio ?? 0); conclusion(`\u68c0\u9a8c\u62a5\u544a\uff1a\u6d4b\u8bd5RankIC ${fmt(x.test_rank_ic||wf.mean_test_rank_ic,3)}, \u6b63IC\u6bd4\u4f8b ${fmt(positive*100,0)}%, \u5e74\u5ea6\u7a33\u5b9a\u6027\u5982\u4e0b\u3002`); root(`<div class="panel-grid">${panel(c1,'\u56e0\u5b50\u56de\u6d4b\u51c0\u503c','\u591a\u7a7a/\u591a\u5934/\u57fa\u51c6',true)}${panel(c2,'RankIC\u4e0e\u5206\u7ec4\u6536\u76ca\u5dee','\u53f3\u8f74\u4e3a\u6536\u76ca\u5dee',true)}</div>`+tableHTML('\u5e74\u5ea6\u7a33\u5b9a\u6027',arr(x.annual_summary),['year','rank_ic','group_spread','long_short_return','long_return','benchmark_return','positive_ic_rate','coverage'])+tableHTML('\u6eda\u52a8\u6837\u672c\u5916\u68c0\u9a8c',wins,['test_period','test','train_rank_ic','test_rank_ic','train_ic','test_ic','decay'])+tableHTML('\u5019\u9009\u56e0\u5b50\u6c60',reports(),['chinese_name','status','test_rank_ic','valid_rank_ic','train_rank_ic','lifecycle_deployment_confidence','complexity'])); factorCurve(c1,x); factorIC(c2,x); }
  function scoreDims(x){ const ic=Math.min(100,Math.max(0,Math.abs(Number(x.test_rank_ic||x.valid_rank_ic||0))*1200)); const conf=Math.max(0,Math.min(100,Number(x.lifecycle_deployment_confidence||0)*100)); const red=Math.max(0,Math.min(100,(1-Number(x.redundancy_max_abs_corr??x.max_abs_corr_to_other_factor??0))*100)); const robust=Math.max(0,Math.min(100,Number(x.walk_positive_ratio||x.purged_positive_ratio||0)*100)); const draw=Math.max(0,Math.min(100,100+Number(x.test_long_short_max_drawdown||x.test_long_max_drawdown||0)*100)); const comp=Math.max(0,Math.min(100,100-Number(x.complexity||0)*3)); return [{label:'\u4fe1\u53f7',value:ic},{label:'\u7a33\u5065',value:robust},{label:'\u4f4e\u76f8\u5173',value:red},{label:'\u7f6e\u4fe1',value:conf},{label:'\u56de\u64a4',value:draw},{label:'\u7b80\u6d01',value:comp}]; }
  function radar(id,d){ const theta=d.map(x=>x.label).concat(d[0].label), r=d.map(x=>x.value).concat(d[0].value); plot(id,[{type:'scatterpolar',r,theta,fill:'toself',name:'\u5f97\u5206',line:{color:'#b42318'},fillcolor:'rgba(180,35,24,.18)'}],{polar:{radialaxis:{visible:true,range:[0,100],gridcolor:'#edf0f2'},angularaxis:{gridcolor:'#edf0f2'}},showlegend:false,margin:{l:32,r:32,t:12,b:32}}); }
  function factorScore(){ const x=selectedFactor(), dims=scoreDims(x), rid=pid('rad'); conclusion('当前因子 '+esc(x.chinese_name||x.name||'--')+'，六维综合证据均值 '+fmNum(dimensions.reduce(function(sum,row){return sum+Number(row.value||0);},0)/Math.max(dimensions.length,1))+' 分，状态 '+esc(fmStatus(x.status||x.lifecycle_state))+'。'); root(`<div class="panel-grid">${panel(rid,'\u7ef4\u5ea6\u96f7\u8fbe\u56fe','0-100\u6807\u51c6\u5316\u5f97\u5206',false)}<section class="workbench-panel"><h2>\u7efc\u5408\u5224\u65ad</h2>${cardHTML(dims.map(d=>({label:d.label,value:d.value,unit:'\u5206'})))}<p>\u7ed3\u8bba\uff1a ${esc(x.diagnosis_cn||x.accepted_type_cn||x.lifecycle_production_ready_reason||'\u7edf\u8ba1\u95e8\u69db\u901a\u8fc7\u540e\uff0c\u9700\u7ed3\u5408\u90e8\u7f72\u7f6e\u4fe1\u5ea6\u4e0e\u5197\u4f59\u63a7\u5236\u4f7f\u7528\u3002')}</p></section></div>`+tableHTML('\u56e0\u5b50\u8bc4\u5206\u8868',reports(),['chinese_name','name','status','production_eligible','lifecycle_state','lifecycle_deployment_confidence','complexity','redundancy_max_abs_corr','test_rank_ic'])); radar(rid,dims); }
  async function factorMemory(){ await needFactor(); const rows=fRows().map(r=>Object.assign({factor_view:'\u67e5\u770b'},r)); clearConclusion(); root(tableHTML('\u56e0\u5b50\u5386\u53f2\u8bb0\u5fc6',rows,['factor_view','source','job_id','created_at','universe','status','target_accepted','candidate_count','accepted_count','elapsed_seconds'])); document.querySelectorAll('[data-factor-view],[data-job-id]').forEach(a=>a.onclick=async(e)=>{e.preventDefault(); const id=a.dataset.factorView||a.dataset.jobId; await factorDetail(id); S.active='factor:expression'; document.querySelectorAll('.nav-item').forEach(x=>x.classList.toggle('is-active',x.dataset.target===S.active)); await render();}); }
  function pill(k,v){ if(v===undefined||v===null||v==='')return ''; const label=(COL[k]||({status:'\u72b6\u6001',confidence:'\u7f6e\u4fe1\u5ea6',complexity:'\u590d\u6742\u5ea6',channel:'\u6765\u6e90\u901a\u9053'}[k])||k); return `<span class="pill">${esc(label)}: ${esc(maybe(v,k))}</span>`; }
  function jobHTML(j){ if(!j||!Object.keys(j).length)return '<p>\u6682\u65e0\u4efb\u52a1\u3002</p>'; const sum=obj(j.summary||j.result||{}), met=obj(sum.backtest_metrics||sum.metrics||{}), sig=obj(sum.latest_signal||{}), steps=arr(j.progress_steps); const ret=(obj(met.full).total_return ?? obj(met.test).total_return ?? j.full_return ?? j.test_return); return `<div class="kpi-grid">${[['\u72b6\u6001',j.status||sum.status],['\u6807\u7684/\u4efb\u52a1',j.code||j.ts_code||j.universe||sum.ts_code||j.job_id],['\u6700\u65b0\u4fe1\u53f7',sig.target_position_instruction||sig.action||j.latest_signal||j.progress],['\u6536\u76ca',ret]].map(x=>`<article class="kpi-card"><small>${esc(x[0])}</small><strong>${esc(maybe(x[1]))}</strong><em><span>\u4efb\u52a1</span><span>${esc(j.job_id||j.id||'')}</span></em></article>`).join('')}</div>${steps.length?`<div class="progress-track">${steps.map(s=>`<span class="progress-step ${esc(s.status||'')}">${esc(s.label||s.id||'')}</span>`).join('')}</div>`:`<p>${esc(j.progress_message||j.progress||'')}</p>`}`; }


  /* r6 overrides: speed, chart hygiene, industry proxies, stock K-line, factor visuals */
  function init(){ bindNav(); tick(); setInterval(tick,30000); loadServices(); loadSnapshot().then(render).catch(e=>conclusion('数据加载失败：'+esc(e.message))); setInterval(loadServices,300000) }
  function colorSignedHtml(h){ return String(h||'').replace(/([+]\d+(?:\.\d+)?%)/g,'<span class="num-pos">$1</span>').replace(/(-\d+(?:\.\d+)?%)/g,'<span class="num-neg">$1</span>') }
  function conclusion(h){ $('core-conclusion').innerHTML=`<span class="eyebrow">${TXT.core}</span><p>${colorSignedHtml(h)}</p>` }
  function plot(id,traces,layout){ const e=$(id); if(!e)return; if(!window.Plotly||!arr(traces).length){ e.innerHTML=`<div class="chart-fallback">${TXT.noData}</div>`; return } const base={font:{family:'Arial,"KaiTi","Microsoft YaHei",sans-serif',size:10,color:'#344054'},paper_bgcolor:'rgba(0,0,0,0)',plot_bgcolor:'rgba(0,0,0,0)',margin:{l:44,r:18,t:12,b:42},hoverlabel:{font:{size:11}}}; Plotly.react(e,traces,Object.assign(base,layout||{}),{responsive:true,displayModeBar:false,staticPlot:false}) }
  function bar(id,rows){ const d=arr(rows).filter(r=>Number.isFinite(Number(r.value))); plot(id,d.length?[{type:'bar',x:d.map(r=>r.label),y:d.map(r=>Number(r.value)),marker:{color:d.map(r=>Number(r.value)>=0?'#c00000':'#168a47')},text:d.map(r=>fmt(r.value)),textposition:'auto'}]:[],{showlegend:false,xaxis:{tickangle:-25,showgrid:false},yaxis:{gridcolor:'#edf0f2',zerolinecolor:'#d8e0e7'}}) }
  function marketCN(x){ const m={'SSE Composite':'上证综指','CSI 300':'沪深300','S&P 500':'标普500','NASDAQ':'纳斯达克','Dow Jones':'道琼斯','Hang Seng':'恒生指数','Nikkei 225':'日经225','KOSPI':'韩国综合指数','Euro Stoxx 50':'欧洲斯托克50','DAX':'德国法兰克福指数','恒生科技':'恒生科技','创业板指':'创业板指','深证成指':'深证成指'}; return m[x]||String(x||'--').replace(' close','') }
  function relabelSeries(s,name){ return Object.assign({},s,{label:name||s.label}) }
  function idMap(list){ return new Map(arr(list).map(s=>[s.id,s])) }
  function byIds(map,ids,labels){ return arr(ids).map((id,i)=>map.get(id)?relabelSeries(map.get(id),labels&&labels[i]):null).filter(Boolean) }
  async function macro(){ const cards=await fetchSeries(['cn_gdp_yoy','cn_cpi_yoy','cn_ppi_yoy','cn_m2_yoy']); const top=[cards.find(s=>s.id==='cn_gdp_yoy'),cards.find(s=>s.id==='cn_cpi_yoy'),cards.find(s=>s.id==='cn_m2_yoy')].filter(Boolean); conclusion(`截至 ${maxDate(top)}, ${top.map(trend).join('；')}。`); const specs=[['增长与生产',['cn_gdp_yoy','cn_industrial_prod_yoy','cn_fai_yoy','cn_pmi_mfg'],['GDP同比','工业增加值同比','固定资产投资同比','制造业PMI'],['cn_pmi_mfg','cn_pmi_non_mfg','cn_lpi','cn_enterprise_boom'],['制造业PMI','非制造业PMI','物流业景气','企业景气']],['需求与消费',['cn_retail_yoy','cn_retail_ytd_yoy','cn_mobile_shipments'],['社零同比','社零累计同比','手机出货量'],['cn_consumer_confidence','cn_consumer_satisfaction','cn_consumer_expectation'],['消费者信心','消费者满意','消费者预期']],['价格与通胀',['cn_cpi_yoy','cn_ppi_yoy','cn_cpi_mom'],['CPI同比','PPI同比','CPI环比'],['cn_agri_wholesale_index','cn_commodity_price_index','cn_construction_material_index','cn_energy_index'],['农产品批发价','大宗商品价格','建材价格','能源价格']],['地产',['cn_new_house_yoy','cn_second_house_yoy'],['新房价格同比','二手房价格同比'],['cn_new_house_mom','cn_second_house_mom'],['新房价格环比','二手房价格环比']],['货币与流动性',['cn_m2_yoy','cn_m1_yoy','cn_m1_m2_gap'],['M2同比','M1同比','M1-M2剪刀差'],['cn_shibor_on','cn_shibor_1w','cn_shibor_1m','cn_lpr_1y'],['Shibor隔夜','Shibor 1周','Shibor 1月','1年LPR']],['信用与财政',['cn_new_credit_month','cn_tsf_increment','cn_new_credit_ytd'],['新增人民币贷款','社融规模增量','新增贷款累计'],['cn_fiscal_revenue_ytd_yoy','cn_tsf_rmb_loan'],['财政收入累计同比','社融人民币贷款']],['外贸与储备',['cn_export_yoy','cn_import_yoy','cn_trade_balance'],['出口同比','进口同比','贸易差额'],['cn_fx_reserves','cn_gold_reserves'],['外汇储备','黄金储备']],['运输与实体高频',['cn_freight_volume_yoy','cn_air_load_factor','cn_electricity_secondary_yoy','cn_electricity_tertiary_yoy'],['货运量同比','民航客座率','第二产业用电同比','第三产业用电同比'],['global_bdi','global_bci','global_bpi','global_bdti'],['BDI','BCI','BPI','BDTI']]]; const all=[...new Set(specs.flatMap(x=>x[1].concat(x[3])))]; const m=idMap(await fetchSeries(all)); const html=[cardHTML(cards.map(s=>({series:s})))]; const draw=[]; specs.forEach(sp=>{ const a=byIds(m,sp[1],sp[2]), b=byIds(m,sp[3],sp[4]); const id1=pid('m'), id2=pid('m'); html.push(`<div class="section-heading"><div><span class="eyebrow">宏观分项</span><h2>${esc(sp[0])}</h2><p>截至 ${maxDate(a.concat(b))}, ${firstPointSeries(a.concat(b),2).map(trend).join('；')}。</p></div></div><div class="panel-grid">${panel(id1,sp[0]+' 第一组指标','同组口径优先，必要时自动右轴',false)}${panel(id2,sp[0]+' 第二组指标','一张图不超过四条线',false)}</div>`); draw.push(()=>lineSmart(id1,a,{max:220}),()=>lineSmart(id2,b,{max:220})) }); root(html.join('')); draw.forEach(f=>f()) }
  function regionLineCN(rows,key){ const order=['A股','美股','港股','韩股','日股','欧股']; const buckets={'A股':['China A','A股'],'美股':['United States','美股'],'港股':['Hong Kong','中国香港','港股'],'韩股':['Korea','韩股'],'日股':['Japan','日股'],'欧股':['Europe','欧股']}; return order.map(name=>{ const rs=rows.filter(r=>buckets[name].some(k=>String(r.region||'').includes(k)||String(r.market||'').includes(k))); const vals=rs.map(r=>Number(r[key])).filter(Number.isFinite); const avgv=vals.length?vals.reduce((a,b)=>a+b,0)/vals.length:null; return `${name} ${avgv===null?'--':signed(avgv)+'%'}(${rs.length})` }).join('；') }
  async function global(){ const baseRows=arr(table('global_markets','global_market_matrix').rows); const supp=await getGlobalSupp(); const rowMap=new Map(); arr(supp.rows).concat(baseRows).forEach(r=>{ const key=marketCN(r.market); if(!rowMap.has(key)) rowMap.set(key,Object.assign({},r,{market_cn:key})) }); const rows=Array.from(rowMap.values()); const asof=maxDate(rows); conclusion(`<span>截至 ${asof}, 最近一日：${regionLineCN(rows,'ret_1d')}。</span><br><span>最近一周：${regionLineCN(rows,'ret_5d')}；合计 ${rows.length} 个指数。</span>`); const close=(await hydrate(ser('global_markets').filter(s=>/_close$/.test(s.id)))).concat(arr(supp.series)).map(s=>relabelSeries(s,marketCN(String(s.label||'').replace(/ close$/,'')))); const best=[...rows].sort((a,b)=>+b.ret_5d-+a.ret_5d)[0]||{}, worst=[...rows].sort((a,b)=>+a.ret_5d-+b.ret_5d)[0]||{}; const p1=pid('g'),p2=pid('g'),p3=pid('g'),p4=pid('g'); root(cardHTML([{label:'周度最强',value:best.ret_5d,unit:'%',as_of:marketCN(best.market)},{label:'周度最弱',value:worst.ret_5d,unit:'%',as_of:marketCN(worst.market)},{label:'指数数量',value:rows.length,unit:'',as_of:asof},{label:'补充指数',value:arr(supp.rows).length,unit:''}])+`<div class="panel-grid">${panel(p1,'主要指数走势','基准=100',true)}${panel(p2,'最近一日收益','A股 / 美股 / 港股 / 韩股 / 日股 / 欧股',false)}${panel(p3,'最近一周收益','A股 / 美股 / 港股 / 韩股 / 日股 / 欧股',false)}${panel(p4,'风险收益图','横轴20日波动，纵轴20日收益',false)}</div>`+tableHTML('全球市场矩阵',rows,['market_cn','region','close','ret_1d','ret_5d','ret_20d','vol_20d','mdd_60d','as_of','source'])); line(p1,pickRegionSeries(close),{rebase:true,max:180}); bar(p2,rows.map(r=>({label:marketCN(r.market),value:r.ret_1d}))); bar(p3,rows.map(r=>({label:marketCN(r.market),value:r.ret_5d}))); scatter(p4,rows.map(r=>({label:marketCN(r.market),x:r.vol_20d,y:r.ret_20d}))) }



  /* r6b overrides: SW industry, news rank, stock kline and AI dual buttons */
  function industryDefs(name){ const m={电子:{ids1:['cn_mobile_shipments','commodity_cu_main_close'],labs1:['手机出货量','铜价'],ids2:['commodity_ag_main_close'],kw:['电子','芯片','半导体','存储','消费电子','光刻']},通信:{ids1:['cn_mobile_shipments','global_bdi'],labs1:['手机出货量','BDI'],ids2:['commodity_cu_main_close'],kw:['通信','5G','光模块','运营商','算力网络']},计算机:{ids1:['cn_pmi_non_mfg','cn_enterprise_boom'],labs1:['非制造业PMI','企业景气'],ids2:['cn_consumer_expectation'],kw:['计算机','AI','人工智能','软件','信创','数据要素']},电力设备:{ids1:['commodity_cu_main_close','commodity_al_main_close'],labs1:['铜价','铝价'],ids2:['cn_electricity_secondary_yoy','cn_energy_index'],kw:['电力设备','新能源','锂电','光伏','储能','风电']},医药生物:{ids1:['cn_consumer_confidence','cn_cpi_yoy'],labs1:['消费者信心','CPI同比'],ids2:['cn_retail_yoy'],kw:['医药','创新药','医疗','医保','CXO','器械']},有色金属:{ids1:['commodity_cu_main_close','commodity_al_main_close','commodity_au_main_close'],labs1:['铜价','铝价','黄金'],ids2:['commodity_ag_main_close'],kw:['有色','铜','铝','黄金','稀土','锂']},银行:{ids1:['cn_shibor_on','cn_shibor_1w','cn_lpr_1y','cn_lpr_5y'],labs1:['Shibor隔夜','Shibor1周','1年LPR','5年LPR'],ids2:['cn_m2_yoy','cn_m1_yoy'],kw:['银行','息差','信贷','存款','贷款','社融']},食品饮料:{ids1:['cn_agri_wholesale_index','cn_retail_yoy'],labs1:['农产品批发价','社零同比'],ids2:['cn_cpi_yoy'],kw:['食品饮料','白酒','啤酒','乳品','消费']},汽车:{ids1:['cn_mobile_shipments','commodity_al_main_close'],labs1:['耐用品需求代理','铝价'],ids2:['commodity_rb_main_close'],kw:['汽车','新能源车','乘用车','智能驾驶','零部件']},煤炭:{ids1:['cn_energy_index','commodity_sc_main_close'],labs1:['能源价格','原油'],ids2:['cn_electricity_secondary_yoy'],kw:['煤炭','动力煤','焦煤','煤价']},石油石化:{ids1:['commodity_sc_main_close','cn_energy_index'],labs1:['原油','能源指数'],ids2:['commodity_ta_main_close'],kw:['石油','石化','原油','炼化','油价']},基础化工:{ids1:['commodity_ta_main_close','commodity_sc_main_close'],labs1:['PTA','原油'],ids2:['cn_commodity_price_index'],kw:['化工','PTA','涨价','化肥','农药']},钢铁:{ids1:['commodity_rb_main_close','commodity_i_main_close'],labs1:['螺纹钢','铁矿石'],ids2:['cn_construction_material_index'],kw:['钢铁','螺纹','铁矿','钢材']},建筑材料:{ids1:['cn_construction_material_index','commodity_rb_main_close'],labs1:['建材指数','螺纹钢'],ids2:['cn_new_house_yoy'],kw:['建材','水泥','玻璃','地产链']},房地产:{ids1:['cn_new_house_yoy','cn_second_house_yoy'],labs1:['新房同比','二手房同比'],ids2:['cn_new_house_mom','cn_second_house_mom'],kw:['房地产','新房','二手房','销售','土拍']},交通运输:{ids1:['cn_freight_volume_yoy','global_bdi','global_bcti'],labs1:['货运量同比','BDI','BCTI'],ids2:['cn_air_load_factor'],kw:['交通运输','航运','快递','航空','港口']},农林牧渔:{ids1:['cn_agri_wholesale_index','commodity_m_main_close'],labs1:['农产品批发价','豆粕'],ids2:['commodity_cf_main_close'],kw:['农业','猪价','养殖','种业','饲料']},纺织服饰:{ids1:['commodity_cf_main_close','cn_retail_yoy'],labs1:['棉花','社零同比'],ids2:['cn_export_yoy'],kw:['纺织','服饰','棉花','出口订单']},商贸零售:{ids1:['cn_retail_yoy','cn_consumer_confidence'],labs1:['社零同比','消费者信心'],ids2:['cn_consumer_expectation'],kw:['商贸','零售','消费','免税','百货']},社会服务:{ids1:['cn_consumer_confidence','cn_passenger_volume'],labs1:['消费者信心','客运量'],ids2:['cn_air_load_factor'],kw:['旅游','酒店','餐饮','出行','景区']},传媒:{ids1:['cn_consumer_expectation','cn_retail_yoy'],labs1:['消费者预期','社零同比'],ids2:['cn_pmi_non_mfg'],kw:['传媒','游戏','影视','广告','短剧']},国防军工:{ids1:['cn_pmi_mfg','cn_industrial_prod_yoy'],labs1:['制造业PMI','工业增加值同比'],ids2:['cn_fai_yoy'],kw:['军工','卫星','航空发动机','无人机','船舶']}}; return m[name]||{ids1:['cn_pmi_mfg','cn_commodity_price_index'],labs1:['制造业PMI','大宗商品价格'],ids2:['cn_retail_yoy'],kw:[name]} }
  function heatSeries(name,kw,newsRows){ const days=[]; for(let i=39;i>=0;i--){ const d=new Date(); d.setDate(d.getDate()-i); days.push(d.toISOString().slice(0,10)) } const pos=days.map(d=>0), neg=days.map(d=>0); arr(newsRows).forEach(r=>{ const day=String(r.published_at||'').slice(0,10), ix=days.indexOf(day); if(ix<0)return; const text=String((r.title||'')+' '+(r.event_type||'')); if(!kw.some(k=>text.includes(k)))return; pos[ix]+=1; if(/风险|下跌|减持|亏损|承压|监管|调查|暴跌/.test(text)) neg[ix]+=1 }); return [{id:'heat_'+name,label:name+'事件热度',unit:'条',data:days.map((d,i)=>({date:d,value:pos[i]})),as_of:days[days.length-1]},{id:'risk_'+name,label:name+'风险热度',unit:'条',data:days.map((d,i)=>({date:d,value:neg[i]})),as_of:days[days.length-1]}] }
  async function sw(){ const rows=arr(table('sw_industries','sw_l1_full_snapshot').rows); const newsRows=arr(table('news_events','news_feed').rows); if(!S.sw){ const want=['电子','通信','计算机','电力设备','医药生物','有色金属','银行','食品饮料']; S.sw=rows.filter(r=>want.includes(r.industry)).map(r=>r.industry) } const sel=rows.filter(r=>S.sw.includes(r.industry)); const asof=maxDate(sel.length?sel:rows); conclusion(`<span>截至 ${asof}, 最近一日核心均值 ${signed(avg(sel,'ret_1d'))}%，最近一周核心均值 ${signed(avg(sel,'ret_5d'))}%。</span><br><span>下方景气图改用各行业专属高频代理与事件热度，不再用统一价格/收益/回撤替代。</span>`); const ids=[...new Set(sel.flatMap(r=>{ const d=industryDefs(r.industry); return d.ids1.concat(d.ids2||[]) }))]; const m=idMap(await fetchSeries(ids)); const html=[control('sw-select',TXT.swPick,rows,'industry',S.sw,'sw-apply','sw-reset'),cardHTML([{label:'已选行业',value:sel.length,unit:'',as_of:asof},{label:'全行业数量',value:rows.length},{label:'日均涨跌',value:avg(sel,'ret_1d'),unit:'%'},{label:'周均涨跌',value:avg(sel,'ret_5d'),unit:'%'}])]; const draw=[]; sel.forEach(r=>{ const d=industryDefs(r.industry), p1=pid('sw'), p2=pid('sw'); const a=byIds(m,d.ids1,d.labs1).slice(0,4); const b=byIds(m,d.ids2||[],d.labs2||[]).concat(heatSeries(r.industry,d.kw,newsRows)).slice(0,4); html.push(`<div class="section-heading"><div><span class="eyebrow">行业景气</span><h2>${esc(r.industry)}</h2><p>截至 ${esc(r.as_of||asof)}, 1d ${signed(r.ret_1d)}%，1w ${signed(r.ret_5d)}%，20d ${signed(r.ret_20d)}%。</p></div></div><div class="panel-grid">${panel(p1,r.industry+' 高频景气代理','行业专属指标，最多四条线',false)}${panel(p2,r.industry+' 事件热度与风险','最近40日新闻关键词热度',false)}</div>`); draw.push(()=>lineSmart(p1,a,{max:220}),()=>lineSmart(p2,b,{max:80})) }); html.push(tableHTML('申万一级行业全量',rows,['code','industry','close','ret_1d','ret_5d','ret_20d','vol_20d','mdd_60d','as_of','source'])); root(html.join('')); $('sw-apply').onclick=async()=>{S.sw=Array.from($('sw-select').selectedOptions).map(x=>x.value); if(!S.sw.length)S.sw=null; await sw()}; $('sw-reset').onclick=async()=>{S.sw=null; await sw()}; draw.forEach(f=>f()) }
  function newsRowsWeek(rows){ const sorted=arr(rows).sort((a,b)=>String(b.published_at).localeCompare(String(a.published_at))); const latest=(sorted[0]&&String(sorted[0].published_at||'').slice(0,10))||''; if(!latest)return sorted; const d=new Date(latest); d.setDate(d.getDate()-7); const cut=d.toISOString().slice(0,10); return sorted.filter(r=>String(r.published_at||'').slice(0,10)>=cut) }
  function industryHeat31(rows){ const swRows=arr(table('sw_industries','sw_l1_full_snapshot').rows); return swRows.map(r=>{ const def=industryDefs(r.industry); const n=arr(rows).filter(x=>def.kw.some(k=>String(x.title||'').includes(k))).length; const score=n+Math.max(0,Number(r.ret_1d)||0)/3+Math.abs(Number(r.vol_20d)||0)/40; return {label:r.industry,value:Number(score.toFixed(2))} }).sort((a,b)=>b.value-a.value) }
  function stockHeatTop(rows){ const map={}; arr(rows).forEach(r=>{ const code=String(r.code||'').trim(); if(!code)return; const label=(r.name||code); map[label]=(map[label]||0)+1 }); if(Object.keys(map).length<10){ arr(table('stock','stock_watchlist').rows).forEach(r=>{ const label=r.name||r.code; map[label]=(map[label]||0)+Math.max(0,Math.abs(Number(r.ret_1d)||0)/2+Math.abs(Number(r.ret_20d)||0)/8) }) } return Object.entries(map).map(([label,value])=>({label,value:Number(value.toFixed?value.toFixed(2):value)})).sort((a,b)=>b.value-a.value).slice(0,10) }
  async function news(){ const rows=arr(table('news_events','news_feed').rows).sort((a,b)=>String(b.published_at).localeCompare(String(a.published_at))); const week=newsRowsWeek(rows); const latest=rows[0]||{}; conclusion(`截至 ${esc(latest.published_at||'--')}, 最新事件：“${esc(latest.title||'暂无标题')}”，来源 ${esc(latest.source||'--')}；最近一周纳入 ${week.length||rows.length} 条事件，重点观察31个申万行业与个股热度集中度。`); const p1=pid('n'),p2=pid('n'); const ih=industryHeat31(week.length?week:rows), sh=stockHeatTop(week.length?week:rows); root(cardHTML([{label:'近一周新闻',value:week.length||rows.length,unit:'',as_of:String(latest.published_at||'').slice(0,10)},{label:'行业覆盖',value:ih.length,unit:'个申万行业'},{label:'个股TOP10',value:sh.length,unit:'个股'},{label:'最新来源',value:rows.length,as_of:latest.source}])+`<div class="panel-grid"><section class="chart-panel wide"><div class="panel-header"><div><h3>新闻滚动</h3><p>最近一周，鼠标可直接上下滚动查看；不再自动快速跑马</p></div></div><div class="news-ticker"><div class="news-list">${(week.length?week:rows).map(newsItem).join('')}</div></div></section>${panel(p1,'行业龙虎榜热度排名','31个申万一级行业，从高到低',false)}${panel(p2,'个股龙虎榜热度TOP10','最近一周事件计数/异动热度',false)}</div>`+tableHTML('新闻明细',week.length?week:rows,['published_at','event_type','code','title','source','url'])); bar(p1,ih); bar(p2,sh) }
  async function aiFillMode(module,subject,context,target,mode){ const el=$(target); if(!el)return; el.innerHTML='<p>AI分析生成中...</p>'; try{ const r=await api('/api/ai/analyze',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({module,subject,context,mode})}); el.innerHTML=r.html||'<p>暂无AI分析结果。</p>' }catch(e){ el.innerHTML='<p class="ai-red">AI暂不可用：'+esc(e.message)+'</p>' } }
  async function drawStockKline(id,code){ try{ const p=await api('/api/stock/ohlc/'+encodeURIComponent(code)+'?limit=180'); const rows=arr(p.rows); const x=rows.map(r=>r.date); plot(id,rows.length?[{type:'candlestick',x,open:rows.map(r=>r.open),high:rows.map(r=>r.high),low:rows.map(r=>r.low),close:rows.map(r=>r.close),name:'日K',increasing:{line:{color:'#c00000'},fillcolor:'#c00000'},decreasing:{line:{color:'#168a47'},fillcolor:'#168a47'}},{type:'bar',x,y:rows.map(r=>r.volume),name:'成交量',yaxis:'y2',marker:{color:'rgba(47,117,181,.22)'}}]:[],{xaxis:{rangeslider:{visible:false},showgrid:false},yaxis:{domain:[.25,1],gridcolor:'#edf0f2'},yaxis2:{domain:[0,.16],showgrid:false},legend:{orientation:'h',y:-.18},hovermode:'x unified'}) }catch(e){ $(id).innerHTML=`<div class="chart-fallback">K线加载失败：${esc(e.message)}</div>` } }
  async function stock(){ const base=S.stockOverride||mod('stock'), rows=arr(table('stock','stock_watchlist').rows); if(!S.stockCode)S.stockCode=(rows[0]&&rows[0].code)||'000001'; const activeRows=arr((arr(base.tables).find(x=>x.id==='stock_watchlist')||{}).rows||rows), r=activeRows[0]||rows[0]||{}; const newsRows=relatedNews(arr(table('news_events','news_feed').rows),r.code||S.stockCode,r.name); const asof=r.as_of||maxDate(activeRows); conclusion(`截至 ${asof}, ${esc(r.name||S.stockCode)} 日涨跌 ${signed(r.ret_1d)}%，周涨跌 ${signed(r.ret_5d)}%，20日涨跌 ${signed(r.ret_20d)}%，20日回撤 ${signed(r.mdd_20d)}%；相关新闻 ${newsRows.length}，最新 ${esc((newsRows[0]||{}).published_at||'--')}。`); const p1=pid('s'),p2=pid('s'); root(`<section class="control-card"><div class="control-grid"><label>${TXT.stockPick}<select id="stock-preset">${rows.map(x=>`<option value="${esc(x.code)}" ${String(x.code)===String(S.stockCode)?'selected':''}>${esc(cnText(x.code))} ${esc(x.name)}</option>`).join('')}</select></label><label style="grid-column:span 2;">${TXT.inputCode}<input id="stock-input" value="${esc(S.stockCode)}"></label><button id="stock-load" class="action-button" type="button">${TXT.loadStock}</button><div class="ai-actions"><button id="stock-ai" class="ghost-button" type="button">智能分析</button><button id="stock-deep" class="ghost-button" type="button">深度报告</button></div></div></section>`+cardHTML([{label:'收盘价',value:r.close??r.qfq_close,unit:'元',as_of:asof},{label:'1周涨跌',value:r.ret_5d,unit:'%'},{label:'20日涨跌',value:r.ret_20d,unit:'%'},{label:'20日回撤',value:r.mdd_20d,unit:'%'}])+`<div id="stock-ai-result" class="ai-panel is-compact"><p>点击“智能分析”生成投资建议；点击“深度报告”生成旧subject六段框架报告。</p></div><div class="panel-grid">${panel(p1,'个股K线行情','东方财富日K：开高低收与成交量',true)}${panel(p2,'自选股风险收益','横轴20日波动，纵轴20日收益',false)}</div><section class="chart-panel wide"><div class="panel-header"><div><h3>个股新闻滚动</h3><p>按代码/名称匹配，鼠标可直接上下滚动</p></div></div><div class="news-ticker stock-news"><div class="news-list">${(newsRows.length?newsRows:arr(table('news_events','news_feed').rows).slice(0,8)).map(newsItem).join('')}</div></div></section>`+tableHTML('个股行情',activeRows,['code','name','close','qfq_close','ret_1d','ret_5d','ret_20d','vol_20d','mdd_20d','turnover','as_of'])+tableHTML('相关个股新闻',newsRows,['published_at','event_type','code','title','source','url'])); $('stock-preset').onchange=()=>{$('stock-input').value=$('stock-preset').value}; $('stock-load').onclick=async()=>{const c=$('stock-input').value.trim(); if(!c)return; S.stockCode=c; try{const p=await api('/api/board/stock/'+encodeURIComponent(c)); S.stockOverride=p.data||null}catch(e){S.stockOverride=null; conclusion('个股加载失败：'+esc(e.message))} await stock()}; const ctx={quote:r,news:newsRows.slice(0,12),watchlist:rows.slice(0,20)}; $('stock-ai').onclick=()=>aiFill('stock',`${r.code||S.stockCode} ${r.name||''}`,ctx,'stock-ai-result'); $('stock-deep').onclick=()=>aiFillMode('stock',`${r.code||S.stockCode} ${r.name||''}`,ctx,'stock-ai-result','deep_report'); drawStockKline(p1,r.code||S.stockCode); scatter(p2,rows.map(x=>({label:x.name||x.code,x:x.vol_20d,y:x.ret_20d}))) }



  /* r6c overrides: factor formula/score and K-line signal cleanup */
  function variableRows(raw){ const dict=factorVarLogic(); const text=String(raw||''); return Object.keys(dict).filter(k=>text.includes(k)).map(k=>({proxy:dict[k][0],name:dict[k][0],formula:dict[k][1],logic:dict[k][2],variable:k})) }
  function formulaHTML(raw){ let x=String(raw||'').replace(/\\_/g,'_'); variableRows(raw).forEach(v=>{ const token=v.variable.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'); x=x.replace(new RegExp(token,'g'),v.name) }); x=x.replace(/\\operatorname\{([^}]+)\}/g,'$1').replace(/\\mathrm\{([^}]+)\}/g,'$1').replace(/\\left|\\right/g,'').replace(/\\cdot/g,'·').replace(/\\Delta/g,'Δ').replace(/\\sigma/g,'σ').replace(/[{}]/g,''); x=esc(x); x=x.replace(/_\{?([0-9A-Za-z\u4e00-\u9fa5]+)\}?/g,'<sub>$1</sub>'); return x }
  function factorExpression(){ const rs=reports(), x=selectedFactor(), formula=x.latex_formula||x.formula||x.expression||'', vars=variableRows(formula); conclusion('当前因子 '+esc(x.chinese_name||x.name||'--')+'，状态 '+esc(fmStatus(x.status||x.lifecycle_state))+'，部署置信度 '+fmPct(x.lifecycle_deployment_confidence)+'。'); root(`<section class="workbench-panel"><h2>${esc(x.chinese_name||x.name||x.factor||'暂无因子')}</h2><div class="pill-row">${pill('status',x.status||x.lifecycle_state)}${pill('confidence',x.lifecycle_deployment_confidence)}${pill('complexity',x.complexity)}${pill('channel',x.channel)}</div><div class="formula-box rendered-formula">${formulaHTML(formula)||'暂无公式'}</div><details class="source-box"><summary>查看LaTeX源码</summary><pre>${esc(formula)}</pre></details></section>`+tableHTML('变量解释表',vars,['name','formula','logic'])+tableHTML('候选因子池',rs,['chinese_name','name','status','channel','test_rank_ic','rank_ic','lifecycle_deployment_confidence','redundancy_max_abs_corr'])) }
  function factorScore(){ const x=selectedFactor(), dims=scoreDims(x), rid=pid('rad'), c1=pid('fc'), c2=pid('fi'); conclusion('当前因子 '+esc(x.chinese_name||x.name||'--')+'，六维综合证据均值 '+fmNum(dimensions.reduce(function(sum,row){return sum+Number(row.value||0);},0)/Math.max(dimensions.length,1))+' 分，状态 '+esc(fmStatus(x.status||x.lifecycle_state))+'。'); root(`<div class="factor-score-layout">${panel(rid,'维度雷达图','0-100标准化得分',false)}<section class="workbench-panel"><h2>综合归因与裁判打分</h2>${cardHTML(dims.map(d=>({label:d.label,value:d.value,unit:'分'})))}<p>结论：${esc(x.diagnosis_cn||x.accepted_type_cn||x.lifecycle_production_ready_reason||'统计门槛通过后，需结合部署置信度与冗余控制使用。')}</p></section></div><div class="factor-visual-grid">${panel(c1,'组合净值与回撤曲线','多空/多头/基准',true)}${panel(c2,'RankIC与分组收益差','右轴为收益差',true)}</div>`+tableHTML('因子评分表',reports(),['chinese_name','name','status','production_eligible','lifecycle_state','lifecycle_deployment_confidence','complexity','redundancy_max_abs_corr','test_rank_ic'])); radar(rid,dims); factorCurve(c1,x); factorIC(c2,x) }
  function factorReport(){ const x=selectedFactor(), wf=obj(x.walk_forward||x.walkforward||x.validation||{}), wins=arr(wf.windows||wf.folds), c1=pid('fc'), c2=pid('fi'), positive=(wf.positive_rate ?? wf.positive_test_ic_ratio ?? 0); const gates=[['搜索可验证',x.production_eligible!==false],['后验研究证据',Number(x.test_rank_ic||0)>0],['独立增量',Number(x.redundancy_max_abs_corr||0)<0.2],['市场中性',true],['多头增强',Number(x.test_rank_ic||0)>=0],['生命周期',String(x.lifecycle_state||x.status||'').includes('accepted')||String(x.status||'').includes('通过')]]; conclusion(`检验报告：测试RankIC ${fmt(x.test_rank_ic||wf.mean_test_rank_ic,3)}，正IC比例 ${fmt(positive*100,0)}%，年度稳定性如下。`); root(`<section class="workbench-panel"><h2>快速初筛</h2><div class="factor-gate-grid">${gates.map(g=>`<div class="factor-gate ${g[1]?'pass':'fail'}"><strong>${esc(g[0])}</strong><br>${g[1]?'通过':'未通过'}</div>`).join('')}</div></section><div class="factor-visual-grid">${panel(c1,'组合净值与回撤曲线','多空/多头/基准',true)}${panel(c2,'RankIC与分组收益差','右轴为收益差',true)}</div>`+tableHTML('年度稳定性',arr(x.annual_summary),['year','rank_ic','group_spread','long_short_return','long_return','benchmark_return','positive_ic_rate','coverage'])+tableHTML('滚动样本外检验',wins,['test_period','test','train_rank_ic','test_rank_ic','train_ic','test_ic','decay'])+tableHTML('候选因子池',reports(),['chinese_name','status','test_rank_ic','valid_rank_ic','train_rank_ic','lifecycle_deployment_confidence','complexity'])); factorCurve(c1,x); factorIC(c2,x) }
  function klineCandle(id,j){ const sum=obj(j.summary), daily=arr(obj(sum.chart_data).daily).slice(-520), nodes=arr(sum.signal_nodes).slice(-80); const x=daily.map(r=>String(r[0]).replace(/(\d{4})(\d{2})(\d{2})/,'$1-$2-$3')); const trace={type:'candlestick',x,open:daily.map(r=>+r[1]),high:daily.map(r=>+r[2]),low:daily.map(r=>+r[3]),close:daily.map(r=>+r[4]),name:'K线',increasing:{line:{color:'#c00000'},fillcolor:'#c00000'},decreasing:{line:{color:'#168a47'},fillcolor:'#168a47'}}; const vol={type:'bar',x,y:daily.map(r=>+r[6]),name:'成交量',yaxis:'y2',marker:{color:'rgba(47,117,181,.22)'}}; const mk=(arrs,name,color,sym)=>({type:'scatter',mode:'markers',name,x:arrs.map(n=>String(n.execution_date||n.date).replace(/(\d{4})(\d{2})(\d{2})/,'$1-$2-$3')),y:arrs.map(n=>n.execution_price||n.price),marker:{symbol:sym,size:9,color}}); const buys=nodes.filter(n=>/buy|hold/.test(String(n.action))).slice(-35), sells=nodes.filter(n=>/sell|reduce/.test(String(n.action))).slice(-35); plot(id,daily.length?[trace,vol,mk(buys,'买入/持有','#168a47','triangle-up'),mk(sells,'卖出/减仓','#c46a08','triangle-down')]:[],{xaxis:{rangeslider:{visible:false},showgrid:false},yaxis:{domain:[.24,1],gridcolor:'#edf0f2'},yaxis2:{domain:[0,.16],showgrid:false},legend:{orientation:'h',y:-.18,font:{size:10}},hovermode:'x unified',margin:{l:44,r:18,t:12,b:44}}) }



  /* r7 overrides: complete SW proxies, stock selection, Chinese formula variables, cleaner signal chart */
  function industryDefs(name){ const m={
    '农林牧渔':{ids1:['cn_agri_wholesale_index','commodity_m_main_close'],labs1:['农产品批发价','豆粕主力'],ids2:['commodity_cf_main_close','cn_cpi_yoy'],labs2:['棉花主力','CPI同比'],kw:['农林牧渔','猪价','养殖','种业','饲料','农产品']},
    '基础化工':{ids1:['cn_commodity_price_index','commodity_ta_main_close'],labs1:['大宗商品价格指数','PTA主力'],ids2:['commodity_sc_main_close','cn_ppi_yoy'],labs2:['原油主力','PPI同比'],kw:['化工','PTA','纯碱','MDI','化肥','原油']},
    '钢铁':{ids1:['commodity_rb_main_close','commodity_i_main_close'],labs1:['螺纹钢主力','铁矿石主力'],ids2:['cn_construction_material_index','cn_fai_yoy'],labs2:['建材指数','固定资产投资同比'],kw:['钢铁','螺纹','热卷','铁矿','焦炭']},
    '有色金属':{ids1:['commodity_cu_main_close','commodity_al_main_close'],labs1:['沪铜主力','沪铝主力'],ids2:['commodity_au_main_close','commodity_ag_main_close'],labs2:['黄金主力','白银主力'],kw:['有色','铜','铝','黄金','白银','锂']},
    '电子':{ids1:['cn_mobile_shipments','cn_pmi_mfg'],labs1:['手机出货量','制造业PMI'],ids2:['cn_export_yoy','cn_industrial_prod_yoy'],labs2:['出口同比','工业增加值同比'],kw:['电子','半导体','芯片','存储','消费电子','AI硬件']},
    '家用电器':{ids1:['cn_retail_yoy','cn_consumer_confidence'],labs1:['社零同比','消费者信心'],ids2:['cn_new_house_yoy','commodity_cu_main_close'],labs2:['新房同比','沪铜主力'],kw:['家电','空调','白电','厨电','地产后周期']},
    '食品饮料':{ids1:['cn_retail_yoy','cn_consumer_confidence'],labs1:['社零同比','消费者信心'],ids2:['cn_cpi_yoy','cn_agri_wholesale_index'],labs2:['CPI同比','农产品批发价'],kw:['食品饮料','白酒','啤酒','乳制品','调味品']},
    '纺织服饰':{ids1:['commodity_cf_main_close','cn_export_yoy'],labs1:['棉花主力','出口同比'],ids2:['cn_retail_yoy','cn_consumer_expectation'],labs2:['社零同比','消费者预期'],kw:['纺织','服饰','棉花','服装','出口订单']},
    '轻工制造':{ids1:['cn_retail_yoy','cn_export_yoy'],labs1:['社零同比','出口同比'],ids2:['cn_new_house_yoy','cn_commodity_price_index'],labs2:['新房同比','大宗商品价格指数'],kw:['轻工','造纸','包装','家具','文娱用品']},
    '医药生物':{ids1:['cn_cpi_yoy','cn_consumer_confidence'],labs1:['CPI同比','消费者信心'],ids2:['cn_pmi_non_mfg','cn_retail_yoy'],labs2:['非制造业PMI','社零同比'],kw:['医药','创新药','医疗器械','CXO','集采','医院']},
    '公用事业':{ids1:['cn_electricity_secondary_yoy','cn_electricity_tertiary_yoy'],labs1:['第二产业用电同比','第三产业用电同比'],ids2:['cn_energy_index','commodity_sc_main_close'],labs2:['能源指数','原油主力'],kw:['公用事业','电力','燃气','水务','火电','绿电']},
    '交通运输':{ids1:['cn_freight_volume_yoy','global_bdi'],labs1:['货运量同比','BDI'],ids2:['cn_air_load_factor','global_bcti'],labs2:['航空客座率','BCTI'],kw:['交通运输','航运','快递','航空','港口','物流']},
    '房地产':{ids1:['cn_new_house_yoy','cn_second_house_yoy'],labs1:['新房同比','二手房同比'],ids2:['cn_new_house_mom','cn_second_house_mom'],labs2:['新房环比','二手房环比'],kw:['房地产','新房','二手房','销售','土拍','地产']},
    '商贸零售':{ids1:['cn_retail_yoy','cn_consumer_confidence'],labs1:['社零同比','消费者信心'],ids2:['cn_consumer_expectation','cn_cpi_yoy'],labs2:['消费者预期','CPI同比'],kw:['商贸','零售','免税','百货','消费']},
    '社会服务':{ids1:['cn_passenger_volume','cn_air_load_factor'],labs1:['客运量','航空客座率'],ids2:['cn_consumer_confidence','cn_pmi_non_mfg'],labs2:['消费者信心','非制造业PMI'],kw:['社会服务','旅游','酒店','餐饮','出行','景区']},
    '综合':{ids1:['cn_pmi_mfg','cn_pmi_non_mfg'],labs1:['制造业PMI','非制造业PMI'],ids2:['cn_retail_yoy','cn_fai_yoy'],labs2:['社零同比','固定资产投资同比'],kw:['综合','多元','产业投资','资产重组']},
    '建筑材料':{ids1:['cn_construction_material_index','commodity_rb_main_close'],labs1:['建材指数','螺纹钢主力'],ids2:['cn_new_house_yoy','cn_fai_yoy'],labs2:['新房同比','固定资产投资同比'],kw:['建材','水泥','玻璃','防水','地产链']},
    '建筑装饰':{ids1:['cn_fai_yoy','cn_construction_material_index'],labs1:['固定资产投资同比','建材指数'],ids2:['commodity_rb_main_close','cn_pmi_mfg'],labs2:['螺纹钢主力','制造业PMI'],kw:['建筑装饰','基建','建筑','工程','订单']},
    '电力设备':{ids1:['commodity_cu_main_close','commodity_al_main_close'],labs1:['沪铜主力','沪铝主力'],ids2:['cn_export_yoy','cn_industrial_prod_yoy'],labs2:['出口同比','工业增加值同比'],kw:['电力设备','新能源','光伏','风电','锂电','储能']},
    '国防军工':{ids1:['cn_pmi_mfg','cn_industrial_prod_yoy'],labs1:['制造业PMI','工业增加值同比'],ids2:['cn_fai_yoy','cn_commodity_price_index'],labs2:['固定资产投资同比','大宗商品价格指数'],kw:['军工','卫星','航空发动机','无人机','船舶','导弹']},
    '计算机':{ids1:['cn_pmi_non_mfg','cn_consumer_expectation'],labs1:['非制造业PMI','消费者预期'],ids2:['cn_m2_yoy','cn_shibor_on'],labs2:['M2同比','隔夜Shibor'],kw:['计算机','AI','软件','云','算力','数据要素']},
    '传媒':{ids1:['cn_consumer_expectation','cn_retail_yoy'],labs1:['消费者预期','社零同比'],ids2:['cn_pmi_non_mfg','cn_cpi_yoy'],labs2:['非制造业PMI','CPI同比'],kw:['传媒','游戏','影视','广告','短剧','出版']},
    '通信':{ids1:['cn_mobile_shipments','cn_pmi_mfg'],labs1:['手机出货量','制造业PMI'],ids2:['cn_fai_yoy','cn_m2_yoy'],labs2:['固定资产投资同比','M2同比'],kw:['通信','5G','光模块','运营商','通信设备','卫星互联网']},
    '银行':{ids1:['cn_new_credit_month','cn_tsf_increment'],labs1:['新增人民币贷款','社融增量'],ids2:['cn_shibor_on','cn_lpr_1y'],labs2:['隔夜Shibor','1年LPR'],kw:['银行','息差','贷款','存款','不良','社融']},
    '非银金融':{ids1:['cn_m2_yoy','cn_shibor_on'],labs1:['M2同比','隔夜Shibor'],ids2:['cn_tsf_corp_bond','cn_tsf_equity'],labs2:['企业债融资','股票融资'],kw:['非银','券商','保险','资管','两融','成交额']},
    '汽车':{ids1:['cn_retail_yoy','cn_consumer_confidence'],labs1:['社零同比','消费者信心'],ids2:['commodity_al_main_close','cn_mobile_shipments'],labs2:['沪铝主力','手机出货量'],kw:['汽车','新能源车','乘用车','整车','零部件','销量']},
    '机械设备':{ids1:['cn_pmi_mfg','cn_industrial_prod_yoy'],labs1:['制造业PMI','工业增加值同比'],ids2:['cn_fai_yoy','commodity_rb_main_close'],labs2:['固定资产投资同比','螺纹钢主力'],kw:['机械','设备','机器人','工程机械','机床','订单']},
    '煤炭':{ids1:['cn_energy_index','commodity_i_main_close'],labs1:['能源指数','铁矿石主力'],ids2:['cn_electricity_secondary_yoy','global_bdi'],labs2:['第二产业用电同比','BDI'],kw:['煤炭','动力煤','焦煤','焦炭','电煤']},
    '石油石化':{ids1:['commodity_sc_main_close','cn_energy_index'],labs1:['原油主力','能源指数'],ids2:['cn_ppi_yoy','global_bcti'],labs2:['PPI同比','BCTI'],kw:['石油石化','原油','炼化','油价','成品油']},
    '环保':{ids1:['cn_fai_yoy','cn_energy_index'],labs1:['固定资产投资同比','能源指数'],ids2:['cn_pmi_non_mfg','cn_electricity_tertiary_yoy'],labs2:['非制造业PMI','第三产业用电同比'],kw:['环保','固废','污水','环卫','节能','碳中和']},
    '美容护理':{ids1:['cn_retail_yoy','cn_consumer_confidence'],labs1:['社零同比','消费者信心'],ids2:['cn_cpi_yoy','cn_consumer_expectation'],labs2:['CPI同比','消费者预期'],kw:['美容护理','化妆品','医美','个护','美妆']}
  }; return m[name]||{ids1:['cn_pmi_mfg','cn_commodity_price_index'],labs1:['制造业PMI','大宗商品价格指数'],ids2:['cn_retail_yoy','cn_m2_yoy'],labs2:['社零同比','M2同比'],kw:[name]}; }

  function stockHeatTop(rows){ const map={}; const add=(label,val)=>{ if(!label)return; map[label]=(map[label]||0)+Number(val||0); }; arr(rows).forEach(r=>{ const code=String(r.code||'').trim(); const label=(r.name||code); if(code)add(label,1); }); arr(table('stock','stock_watchlist').rows).forEach(r=>{ const label=r.name||r.code; const score=1+Math.abs(Number(r.ret_1d)||0)/2+Math.abs(Number(r.ret_20d)||0)/10+Math.abs(Number(r.vol_20d)||0)/50; add(label,score); }); return Object.entries(map).map(([label,value])=>({label,value:Number(value.toFixed(2))})).sort((a,b)=>b.value-a.value).slice(0,10); }

  function variableRows(raw){ const dict={op_yoy:['营收同比','排序(营收同比)','经营动量与景气改善'],large_order_balance:['大单资金净额','时间序列均值3(排序(大单资金净额))','资金流持续性'],pb:['市净率','-排序(市净率)','估值安全边际'],base_low_crowding:['低拥挤度','排序(低拥挤度)','避免拥挤交易'],base_event_risk:['事件风险','-排序(事件风险)','降低负面事件暴露']}; const text=String(raw||''); const order=['op_yoy','large_order_balance','pb','base_low_crowding','base_event_risk']; return order.filter(k=>text.includes(k)||/\bX[1-5]\b/.test(text)).map(k=>({proxy:dict[k][0],name:dict[k][0],formula:dict[k][1],logic:dict[k][2],variable:k})); }
  function formulaHTML(raw){ let x=String(raw||'').replace(/\\_/g,'_'); const repl={op_yoy:'营收同比',large_order_balance:'大单资金净额',pb:'市净率',base_low_crowding:'低拥挤度',base_event_risk:'事件风险',X1:'营收同比',X2:'大单资金净额',X3:'市净率',X4:'低拥挤度',X5:'事件风险',GraphConceptResidual:'图概念残差',TSMean:'时间序列均值'}; Object.keys(repl).sort((a,b)=>b.length-a.length).forEach(k=>{ x=x.replace(new RegExp(k.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'),'g'),repl[k]); }); x=x.replace(/\\operatorname\{([^}]+)\}/g,'$1').replace(/\\mathrm\{([^}]+)\}/g,'$1').replace(/\\left|\\right/g,'').replace(/\\cdot/g,'·').replace(/\\Delta/g,'Δ').replace(/\\sigma/g,'σ').replace(/[{}]/g,''); x=x.replace(/rank_?industry/g,'行业内排序').replace(/rank_?行业/g,'行业内排序').replace(/_industry/g,'_行业').replace(/\brank\(/g,'排序('); x=esc(x); x=x.replace(/_\{?([0-9A-Za-z\u4e00-\u9fa5]+)\}?/g,'<sub>$1</sub>'); return x; }

  function factorExpression(){ const rs=reports(), x=selectedFactor(), formula=x.latex_formula||x.formula||x.expression||'', vars=variableRows(formula); conclusion('当前因子 '+esc(x.chinese_name||x.name||'--')+'，状态 '+esc(fmStatus(x.status||x.lifecycle_state))+'，部署置信度 '+fmPct(x.lifecycle_deployment_confidence)+'。'); root(`<section class="workbench-panel"><h2>${esc(x.chinese_name||x.name||x.factor||'暂无因子')}</h2><div class="pill-row">${pill('状态',x.status||x.lifecycle_state)}${pill('置信度',x.lifecycle_deployment_confidence)}${pill('复杂度',x.complexity)}${pill('来源通道',x.channel)}</div><div class="formula-box rendered-formula">${formulaHTML(formula)||'暂无公式'}</div><details class="source-box"><summary>查看LaTeX源码</summary><pre>${esc(formula)}</pre></details></section>`+tableHTML('中文变量解释表',vars,['name','formula','logic'])+tableHTML('候选因子池',rs,['chinese_name','name','status','channel','test_rank_ic','rank_ic','lifecycle_deployment_confidence','redundancy_max_abs_corr'])); }

  async function stock(){ const base=S.stockOverride||mod('stock'), rows=arr(table('stock','stock_watchlist').rows); if(!S.stockCode)S.stockCode=(rows[0]&&rows[0].code)||'000001'; const baseRows=arr((arr(base.tables).find(x=>x.id==='stock_watchlist')||{}).rows||rows); const record=obj(base.record||{}); const selectedCode=digits(S.stockCode); const r=(record.code?record:baseRows.find(x=>digits(x.code)===selectedCode)||rows.find(x=>digits(x.code)===selectedCode)||baseRows[0]||rows[0]||{}); const newsRows=relatedNews(arr(table('news_events','news_feed').rows),r.code||S.stockCode,r.name); const asof=r.as_of||maxDate(baseRows.length?baseRows:rows); conclusion(`截至 ${asof}, ${esc(r.name||S.stockCode)} 日涨跌 ${signed(r.ret_1d)}%，周涨跌 ${signed(r.ret_5d)}%，20日涨跌 ${signed(r.ret_20d)}%，20日回撤 ${signed(r.mdd_20d)}%；相关新闻 ${newsRows.length}，最新 ${esc((newsRows[0]||{}).published_at||'--')}。`); const p1=pid('s'),p2=pid('s'); root(`<section class="control-card"><div class="control-grid"><label>${TXT.stockPick}<select id="stock-preset">${rows.map(x=>`<option value="${esc(x.code)}" ${digits(x.code)===selectedCode?'selected':''}>${esc(cnText(x.code))} ${esc(x.name)}</option>`).join('')}</select></label><label style="grid-column:span 2;">${TXT.inputCode}<input id="stock-input" value="${esc(S.stockCode)}"></label><button id="stock-load" class="action-button" type="button">${TXT.loadStock}</button><div class="ai-actions"><button id="stock-ai" class="ghost-button" type="button">智能分析</button><button id="stock-deep" class="ghost-button" type="button">深度报告</button></div></div></section>`+cardHTML([{label:'收盘价',value:r.close??r.qfq_close,unit:'元',as_of:asof},{label:'1周涨跌',value:r.ret_5d,unit:'%'},{label:'20日涨跌',value:r.ret_20d,unit:'%'},{label:'20日回撤',value:r.mdd_20d,unit:'%'}])+`<div id="stock-ai-result" class="ai-panel is-compact"><p>点击“智能分析”生成投资建议；点击“深度报告”生成旧subject六段框架报告。</p></div><div class="panel-grid">${panel(p1,'个股K线行情','日K开高低收与成交量',true)}${panel(p2,'自选股风险收益','横轴20日波动，纵轴20日收益',false)}</div><section class="chart-panel wide"><div class="panel-header"><div><h3>个股新闻滚动</h3><p>按代码/名称匹配，鼠标可直接上下滚动</p></div></div><div class="news-ticker stock-news"><div class="news-list">${(newsRows.length?newsRows:arr(table('news_events','news_feed').rows).slice(0,8)).map(newsItem).join('')}</div></div></section>`+tableHTML('个股行情',baseRows.length?baseRows:rows,['code','name','close','qfq_close','ret_1d','ret_5d','ret_20d','vol_20d','mdd_20d','turnover','as_of'])+tableHTML('相关个股新闻',newsRows,['published_at','event_type','code','title','source','url'])); $('stock-preset').onchange=()=>{$('stock-input').value=$('stock-preset').value}; $('stock-load').onclick=async()=>{const c=$('stock-input').value.trim(); if(!c)return; S.stockCode=c; try{const p=await api('/api/board/stock/'+encodeURIComponent(c)); S.stockOverride=p.data||null}catch(e){S.stockOverride=null; conclusion('个股加载失败：'+esc(e.message))} await stock()}; const ctx={quote:r,news:newsRows.slice(0,12),watchlist:rows.slice(0,20)}; $('stock-ai').onclick=()=>aiFill('stock',`${r.code||S.stockCode} ${r.name||''}`,ctx,'stock-ai-result'); $('stock-deep').onclick=()=>aiFillMode('stock',`${r.code||S.stockCode} ${r.name||''}`,ctx,'stock-ai-result','deep_report'); await drawStockKline(p1,r.code||S.stockCode); scatter(p2,rows.map(x=>({label:x.name||x.code,x:x.vol_20d,y:x.ret_20d}))); }

  function klineCandle(id,j){ const sum=obj(j.summary), daily=arr(obj(sum.chart_data).daily).slice(-380), allNodes=arr(sum.signal_nodes); const x=daily.map(r=>String(r[0]).replace(/(\d{4})(\d{2})(\d{2})/,'$1-$2-$3')); const start=x[0]||''; const nodes=allNodes.filter(n=>String(n.execution_date||n.date).replace(/(\d{4})(\d{2})(\d{2})/,'$1-$2-$3')>=start); const trace={type:'candlestick',x,open:daily.map(r=>+r[1]),high:daily.map(r=>+r[2]),low:daily.map(r=>+r[3]),close:daily.map(r=>+r[4]),name:'K线',increasing:{line:{color:'#c00000'},fillcolor:'#c00000'},decreasing:{line:{color:'#168a47'},fillcolor:'#168a47'}}; const vol={type:'bar',x,y:daily.map(r=>+r[6]),name:'成交量',yaxis:'y2',marker:{color:'rgba(47,117,181,.20)'}}; const nodeDate=n=>String(n.execution_date||n.date).replace(/(\d{4})(\d{2})(\d{2})/,'$1-$2-$3'); const buys=nodes.filter(n=>/buy|add|买入|加仓/.test(String(n.action))).slice(-25), sells=nodes.filter(n=>/sell|reduce|卖出|减仓/.test(String(n.action))).slice(-25); const mk=(arrs,name,color,sym)=>({type:'scatter',mode:'markers',name,x:arrs.map(nodeDate),y:arrs.map(n=>n.execution_price||n.price),marker:{symbol:sym,size:10,color,line:{width:1,color:'#fff'}}}); plot(id,daily.length?[trace,vol,mk(buys,'买入/加仓','#168a47','triangle-up'),mk(sells,'卖出/减仓','#c46a08','triangle-down')]:[],{xaxis:{rangeslider:{visible:false},showgrid:false},yaxis:{domain:[.24,1],gridcolor:'#edf0f2'},yaxis2:{domain:[0,.16],showgrid:false},legend:{orientation:'h',y:-.18,font:{size:10}},hovermode:'x unified',margin:{l:44,r:18,t:12,b:44}}); }

  /* r8: stable navigation, faster cached first paint, safe hidden stamps, larger chart fonts */
  const SNAPSHOT_CACHE_KEY = 'quant-agent:snapshot:' + String((window.APP_BOOT||{}).version||'v');
  let navBusy = false;
  function readSnapshotCache(){ try{ const raw=sessionStorage.getItem(SNAPSHOT_CACHE_KEY); if(!raw)return null; const o=JSON.parse(raw); if(!o || !o.snapshot || Date.now()-Number(o.ts||0)>1800000)return null; return o.snapshot; }catch(_){ return null; } }
  function writeSnapshotCache(){ try{ if(S.snapshot)sessionStorage.setItem(SNAPSHOT_CACHE_KEY, JSON.stringify({ts:Date.now(), snapshot:S.snapshot})); }catch(_){ } }
  function setText(id,value){ const el=$(id); if(el)el.textContent=value; }
  function stamps(x){ setText('as-of',(x&&x.as_of)||'--'); setText('generated-at',(x&&x.generated_at)||'--'); }
  function init(){ bindNav(); tick(); setInterval(tick,30000); loadServices(); const cached=readSnapshotCache(); if(cached){ S.snapshot=cached; render().catch(e=>conclusion('缓存渲染失败：'+esc(e.message))); } loadSnapshot().then(()=>{ writeSnapshotCache(); if(!cached) return render(); }).catch(e=>{ if(!cached) conclusion('数据加载失败：'+esc(e.message)); }); setInterval(loadServices,300000); }
  function bindNav(){ document.querySelectorAll('.nav-item').forEach(b=>{ if(b.dataset.boundR8)return; b.dataset.boundR8='1'; b.addEventListener('click',async(e)=>{ if(!e.isTrusted)return; e.preventDefault(); const target=b.dataset.target; if(!target || target===S.active || navBusy)return; navBusy=true; try{ S.active=target; document.querySelectorAll('.nav-item').forEach(x=>x.classList.toggle('is-active',x===b)); window.scrollTo({top:0,left:0,behavior:'auto'}); await render(); } finally { navBusy=false; } }); }); }
  function cell(r,c){ const v=obj(r)[c]; if(c==='view'&&r.job_id) return `<td><button type="button" class="link-button" data-kline-view="${esc(r.job_id)}">查看</button></td>`; if(c==='factor_view'&&r.job_id) return `<td><button type="button" class="link-button" data-factor-view="${esc(r.job_id)}">查看</button></td>`; if(c==='job_id'&&v) return `<td><button type="button" class="link-button" data-job-id="${esc(v)}">${esc(v)}</button></td>`; if(c==='url'&&v) return `<td><a href="${esc(v)}" target="_blank" rel="noreferrer">${TXT.open}</a></td>`; return `<td>${esc(maybe(v,c))}</td>`; }
  function plot(id,traces,layout){ const e=$(id); if(!e)return; if(!window.Plotly||!arr(traces).length){ e.innerHTML=`<div class="chart-fallback">${TXT.noData}</div>`; return } const base={font:{family:'Arial,"KaiTi","Microsoft YaHei",sans-serif',size:12,color:'#344054'},paper_bgcolor:'rgba(0,0,0,0)',plot_bgcolor:'rgba(0,0,0,0)',margin:{l:48,r:20,t:14,b:46},hoverlabel:{font:{family:'KaiTi,STKaiti,\"Kaiti SC\",\"Microsoft YaHei\",serif',size:13}}}; Plotly.react(e,traces,Object.assign(base,layout||{}),{responsive:true,displayModeBar:false,staticPlot:false}) }
  async function renderKline(v){ const h=HEAD['kline:'+v]||HEAD['kline:home']; header(h[0],h[1],'K线记忆学习'); setText('as-of','服务'); setText('generated-at','按需生成'); if(v==='home')return await klineHome(); if(v==='learn')return await klineLearn(); if(v==='backtest')return await klineBacktest(); if(v==='history')return await klineHistory(); }
  async function renderFactor(v){ const h=HEAD['factor:'+v]||HEAD['factor:home']; header(h[0],h[1],'LLM因子挖掘'); setText('as-of','服务'); setText('generated-at','按需生成'); if(v==='home')return await factorHome(); if(v==='memory')return await factorMemory(); await factorDetail(); if(v==='expression')return factorExpression(); if(v==='report')return factorReport(); if(v==='score')return factorScore(); }
  async function klineHistory(){ await needKline(); clearConclusion(); const rows=S.kline.history.map(r=>Object.assign({view:'查看'},r)); root(tableHTML('K线历史记录',rows,['created_at','code','as_of','status','analysis_depth','holding_days','test_return','max_drawdown','view','job_id'])); document.querySelectorAll('[data-kline-view]').forEach(a=>a.onclick=async(e)=>{ e.preventDefault(); if(!e.isTrusted || navBusy)return; navBusy=true; try{ await loadKlineJob(a.dataset.klineView); S.active='kline:learn'; document.querySelectorAll('.nav-item').forEach(x=>x.classList.toggle('is-active',x.dataset.target===S.active)); window.scrollTo({top:0,left:0,behavior:'auto'}); await render(); } finally { navBusy=false; } }); }
  async function factorMemory(){ await needFactor(); const rows=fRows().map(r=>Object.assign({factor_view:'查看'},r)); clearConclusion(); root(tableHTML('因子历史记忆',rows,['factor_view','source','job_id','created_at','universe','status','target_accepted','candidate_count','accepted_count','elapsed_seconds'])); document.querySelectorAll('[data-factor-view],[data-job-id]').forEach(a=>a.onclick=async(e)=>{ e.preventDefault(); if(!e.isTrusted || navBusy)return; navBusy=true; try{ const id=a.dataset.factorView||a.dataset.jobId; await factorDetail(id); S.active='factor:expression'; document.querySelectorAll('.nav-item').forEach(x=>x.classList.toggle('is-active',x.dataset.target===S.active)); window.scrollTo({top:0,left:0,behavior:'auto'}); await render(); } finally { navBusy=false; } }); }
  /* r9: live view cache, queued Plotly rendering, chart-skill palette and Chinese labels */
  const CHART_PALETTE = Object.freeze(['#c00000','#ffc000','#2f75b5','#808080','#ed7d31','#7030a0','#00b050','#5b9bd5','#a5a5a5','#ff0000']);
  const VIEW_CACHE = new Map();
  const VIEW_META = new Map();
  const VIEW_TOUCH = new Map();
  const VIEW_CACHE_LIMIT = 8;
  const PLOT_QUEUE = [];
  const API_MEMO = new Map();
  let displayedView = null;
  let plotRunning = false;
  let prefetchStarted = false;
  let pendingNavTarget = null;

  TXT.factorHome = 'LLM因子挖掘';
  HEAD['factor:home'][0] = 'LLM因子挖掘';
  Object.assign(COL,{
    market_cn:'市场', spot:'现货价格', near_contract:'近月合约', near_price:'近月价格',
    dominant_contract:'主力合约', dominant_price:'主力价格', near_basis:'近月基差',
    dominant_basis:'主力基差', near_basis_rate:'近月基差率', dominant_basis_rate:'主力基差率',
    indicator:'指标', value:'数值', unit:'单位', change:'变化', frequency:'频率',
    category:'类别', description:'说明', module:'板块', submodule:'细分板块',
    data_state:'数据状态', available:'是否可用', formula:'计算公式', logic:'经济逻辑',
    proxy:'代理变量', variable:'变量', rank_ic:'秩相关系数', test_rank_ic:'测试集秩相关系数',
    valid_rank_ic:'验证集秩相关系数', train_rank_ic:'训练集秩相关系数',
    train_ic:'训练集信息系数', test_ic:'测试集信息系数', positive_ic_rate:'正信息系数占比',
    lifecycle_deployment_confidence:'部署置信度', redundancy_max_abs_corr:'最大冗余相关系数',
    production_eligible:'是否可生产', lifecycle_state:'生命周期状态', job_id:'任务编号',
    elapsed_seconds:'耗时（秒）', url:'链接', calmar:'卡玛比率', sharpe:'夏普比率',
    event_id:'事件编号', score:'得分', count:'数量', weight:'权重', direction:'方向',
    ret:'收益', return:'收益', benchmark:'基准', drawdown:'回撤', volatility:'波动率'
  });

  const COMMODITY_CN = Object.freeze({
    AU:'黄金',AG:'白银',CU:'沪铜',AL:'沪铝',RB:'螺纹钢',I:'铁矿石',SC:'原油',TA:'精对苯二甲酸',
    ZN:'沪锌',NI:'沪镍',SN:'沪锡',PB:'沪铅',SS:'不锈钢',HC:'热轧卷板',JM:'焦煤',J:'焦炭',
    MA:'甲醇',PP:'聚丙烯',L:'聚乙烯',V:'聚氯乙烯',RU:'天然橡胶',BU:'沥青',FG:'玻璃',
    SA:'纯碱',CF:'棉花',SR:'白糖',M:'豆粕',Y:'豆油',P:'棕榈油',C:'玉米',A:'豆一',
    B:'豆二',OI:'菜籽油',RM:'菜籽粕',AP:'苹果',CJ:'红枣',PK:'花生'
  });
  const MARKET_CN_R9 = Object.freeze({
    'SSE Composite':'上证综合指数','CSI 300':'沪深300指数','S&P 500':'标普500指数',
    'NASDAQ':'纳斯达克综合指数','纳斯达克':'纳斯达克综合指数','纳斯达克指数':'纳斯达克综合指数','纳斯达克综合':'纳斯达克综合指数',
    'Dow Jones':'道琼斯工业指数','道琼斯':'道琼斯工业指数','道琼斯工业':'道琼斯工业指数','Hang Seng':'恒生指数',
    'Nikkei 225':'日经225指数','日经225':'日经225指数','KOSPI':'韩国综合指数','韩国综合':'韩国综合指数','韩国KOSPI':'韩国综合指数',
    'Euro Stoxx 50':'欧洲蓝筹50指数','欧洲斯托克50':'欧洲蓝筹50指数','欧洲蓝筹50':'欧洲蓝筹50指数',
    'DAX':'德国法兰克福指数','德国DAX':'德国法兰克福指数','Hang Seng Tech':'恒生科技指数','China A':'中国内地股票',
    'United States':'美国股票','Hong Kong':'中国香港股票','Korea':'韩国股票',
    'Japan':'日本股票','Europe':'欧洲股票'
  });

  function commodityCN(value){
    const raw=String(value==null?'':value).trim();
    if(COMMODITY_CN[raw.toUpperCase()]) return COMMODITY_CN[raw.toUpperCase()];
    const match=raw.match(/^([A-Za-z]{1,2})(\d{3,4})$/);
    if(match && COMMODITY_CN[match[1].toUpperCase()]) return COMMODITY_CN[match[1].toUpperCase()]+match[2]+'合约';
    return raw;
  }
  function marketCN(value){
    const raw=String(value==null?'':value).replace(/\s+close$/i,'').trim();
    return MARKET_CN_R9[raw] || raw;
  }
  function cnText(value){
    let s=String(value==null?'':value);
    const exact=MARKET_CN_R9[s.trim()];
    if(exact) return exact;
    const pairs=[
      [/(\d{6})\.SZ(?=$|[^A-Za-z0-9])/g,'$1（深市）'],[/(\d{6})\.SH(?=$|[^A-Za-z0-9])/g,'$1（沪市）'],[/(\d{6})\.BJ(?=$|[^A-Za-z0-9])/g,'$1（北交所）'],
      [/\bGraphConceptResidual\b/g,'图概念残差'],[/\bTSMean\b/g,'时间序列均值'],[/\bLaTeX\b/g,'数学公式'],
      [/\bEastmoney\b/gi,'东方财富'],[/\bAKShare\b/g,'免费金融数据接口'],[/\bYahoo\b/gi,'雅虎财经'],
      [/\bOpenAI\b/g,'智能模型'],[/\bGPT\b/g,'智能模型'],[/\bLLM\b/g,'LLM'],[/\bAI\b/g,'智能'],
      [/\bRankIC\b/gi,'秩相关系数'],[/\bIC\b/g,'信息系数'],[/\bPBO\b/g,'回测过拟合概率'],
      [/\bDSR\b/g,'去偏夏普概率'],[/\bCalmar\b/gi,'卡玛比率'],[/\bSharpe\b/gi,'夏普比率'],
      [/\bGDP\b/g,'国内生产总值'],[/\bCPI\b/g,'居民消费价格'],[/\bPPI\b/g,'工业生产者出厂价格'],
      [/\bPMI\b/g,'采购经理指数'],[/\bM2\b/g,'广义货币'],[/\bM1\b/g,'狭义货币'],
      [/\bShibor\b/gi,'上海银行间同业拆借利率'],[/\bLPR\b/g,'贷款市场报价利率'],
      [/\bBDTI\b/g,'波罗的海成品油轮指数'],[/\bBCTI\b/g,'波罗的海原油轮指数'],
      [/\bBDI\b/g,'波罗的海干散货指数'],[/\bBCI\b/g,'海岬型船运指数'],[/\bBPI\b/g,'巴拿马型船运指数'],
      [/\bKOSPI\b/g,'韩国综合指数'],[/\bNASDAQ\b/g,'纳斯达克指数'],[/\bSTOXX\s*50\b/gi,'欧洲蓝筹50指数'],
      [/\bDAX\b/g,'德国法兰克福指数'],[/\bS&P\s*500\b/g,'标普500指数'],
      [/\bSSE\s*Composite\b/gi,'上证综合指数'],[/\bCSI\s*300\b/gi,'沪深300指数'],
      [/\bTOP\s*10\b/gi,'前十'],[/\bALL_A\b/g,'全部A股'],[/\bCSI800_ENH\b/g,'中证800增强池'],[/\bCSI2000_ENH\b/g,'中证2000增强池'],[/\bAPI\b/g,'数据接口'],[/\bURL\b/g,'链接'],[/\bID\b/g,'编号'],
      [/\btrain\b/gi,'训练集'],[/\bvalid\b/gi,'验证集'],[/\btest\b/gi,'测试集'],[/\bfull\b/gi,'全样本'],
      [/\bstatus\b/gi,'状态'],[/\bsource\b/gi,'来源'],[/\bclose\b/gi,'收盘'],[/\bright\b/gi,'右轴'],
      [/\b1d\b/gi,'1日'],[/\b1w\b/gi,'1周'],[/\b20d\b/gi,'20日'],[/\b60d\b/gi,'60日']
    ];
    pairs.forEach(function(pair){ s=s.replace(pair[0],pair[1]); });
    s=s.replace(/\b(AU|AG|CU|AL|RB|SC|TA|ZN|NI|SN|PB|SS|HC|JM|MA|PP|RU|BU|FG|SA|CF|SR|RM|AP|CJ|PK)\b/g,function(m){return COMMODITY_CN[m]||m;});
    return s;
  }
  function sourceText(value){
    const s=String(value==null?'':value);
    if(s==='account') return '账户记录';
    if(s==='server'||s==='server_run') return '服务端记录';
    if(/Eastmoney/i.test(s)) return '东方财富公开行情接口';
    if(/Yahoo/i.test(s)) return '雅虎财经公开行情接口';
    if(/AKShare/i.test(s)) return '免费金融数据接口';
    if(/BaoStock/i.test(s)) return '证券行情公开接口';
    if(/Tushare/i.test(s)) return '金融数据接口';
    return cnText(s);
  }
  function valueText(value){
    const s=String(value==null?'':value);
    const m={
      yes:'是',no:'否',true:'是',false:'否',ready:'已就绪',check:'待检查',running:'运行中',
      queued:'排队中',ok:'正常',done:'完成',completed:'完成',failed:'失败',error:'错误',
      available:'可用',accepted:'通过',rejected:'未通过',train:'训练集',valid:'验证集',
      test:'测试集',full:'全样本',fast:'快速',standard:'标准',deep:'深度',balanced:'平衡',
      conservative:'保守',aggressive:'积极',ALL_A:'全部沪深股票',
      nested_orthogonal_complement_seed:'嵌套正交补充种子',
      llm_hypothesis_generation:'大模型假设生成',account:'账户记录',server:'服务端记录',
      server_run:'服务端记录',partial:'部分可用',warning:'提醒',stale:'待更新'
    };
    return m[s] || m[s.toLowerCase()] || cnText(s);
  }
  function seriesLabel(series){
    const raw=String(series && (series.label||series.name||series.id) || '指标');
    const direct={
      'SSE Composite close':'上证综合指数收盘','CSI 300 close':'沪深300指数收盘',
      'S&P 500 close':'标普500指数收盘','NASDAQ close':'纳斯达克指数收盘',
      'Dow Jones close':'道琼斯工业指数收盘','Hang Seng close':'恒生指数收盘',
      'KOSPI close':'韩国综合指数收盘','Nikkei 225 close':'日经225指数收盘',
      'Euro Stoxx 50 close':'欧洲蓝筹50指数收盘','DAX close':'德国法兰克福指数收盘'
    };
    return cnText(direct[raw]||raw);
  }
  function fieldLabel(key){
    if(COL[key]) return cnText(COL[key]);
    const tokens={
      spot:'现货',near:'近月',dominant:'主力',contract:'合约',price:'价格',basis:'基差',
      rate:'比率',return:'收益',ret:'收益',vol:'波动',mdd:'最大回撤',drawdown:'回撤',
      close:'收盘',open:'开盘',high:'最高',low:'最低',volume:'成交量',amount:'成交额',
      time:'时间',date:'日期',created:'创建',published:'发布',accepted:'通过',
      candidate:'候选',count:'数量',confidence:'置信度',complexity:'复杂度',
      coverage:'覆盖率',score:'得分',state:'状态',type:'类型',period:'区间',
      annual:'年化',total:'累计',benchmark:'基准',long:'多头',short:'空头'
    };
    const parts=String(key||'').split(/[_\s]+/).filter(Boolean);
    const result=parts.map(function(part){return tokens[part.toLowerCase()]||'';}).join('');
    return result || '数据字段';
  }
  function displayValue(value,column){
    if(column==='source') return sourceText(value);
    if(column==='symbol') return commodityCN(value);
    if(column==='near_contract'||column==='dominant_contract') return commodityCN(value);
    if(column==='market'||column==='market_cn'||column==='region') return marketCN(value);
    return valueText(value);
  }
  function maybe(value,column){
    if(value===null||value===undefined||value==='') return '--';
    if(column==='source') return sourceText(value);
    if(column==='symbol'||column==='near_contract'||column==='dominant_contract'||column==='market'||column==='market_cn'||column==='region') return displayValue(value,column);
    if(column && (['code','event_id','bits'].includes(column)||/(^|_)id$/i.test(column))) return String(value);
    if(typeof value==='number') return fmt(value,2);
    if(typeof value==='boolean') return value?'是':'否';
    if(/^-?\d+(\.\d+)?$/.test(String(value))&&String(value).length<12) return fmt(Number(value),2);
    return valueText(value);
  }
  function localizeTree(rootNode){
    if(!rootNode) return;
    const walker=document.createTreeWalker(rootNode,NodeFilter.SHOW_TEXT);
    const nodes=[];
    while(walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach(function(node){
      const parent=node.parentElement;
      if(!parent||parent.closest('.formula-box,.preserve-acronym,pre,code,script,style')) return;
      if(/[A-Za-z]/.test(node.nodeValue||'')) node.nodeValue=cnText(node.nodeValue);
    });
  }
  function colorizeSigned(rootNode){
    if(!rootNode) return;
    const walker=document.createTreeWalker(rootNode,NodeFilter.SHOW_TEXT);
    const nodes=[];
    while(walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach(function(node){
      const text=node.nodeValue||'';
      const re=/(^|[^\d-])([+-]\d+(?:,\d{3})*(?:\.\d+)?%?)/g;
      if(!re.test(text)) return;
      re.lastIndex=0;
      const frag=document.createDocumentFragment();
      let last=0;
      text.replace(re,function(full,prefix,number,offset){
        frag.appendChild(document.createTextNode(text.slice(last,offset)+prefix));
        const span=document.createElement('span');
        span.className=number.charAt(0)==='+'?'value-up':'value-down';
        span.textContent=number;
        frag.appendChild(span);
        last=offset+full.length;
        return full;
      });
      frag.appendChild(document.createTextNode(text.slice(last)));
      node.parentNode.replaceChild(frag,node);
    });
  }
  function colorizeUnsignedMetrics(rootNode){
    if(!rootNode) return;
    const walker=document.createTreeWalker(rootNode,NodeFilter.SHOW_TEXT);
    const nodes=[];
    while(walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach(function(node){
      const parent=node.parentElement;
      if(!parent||parent.closest('.value-up,.value-down')) return;
      const text=node.nodeValue||'';
      const re=/(\d+(?:,\d{3})*(?:\.\d+)?)(%|亿元|亿美元|万部|万盎司|指数|点|元|倍)/g;
      if(!re.test(text)) return;
      re.lastIndex=0;
      const frag=document.createDocumentFragment();
      let last=0;
      text.replace(re,function(full,number,unit,offset){
        frag.appendChild(document.createTextNode(text.slice(last,offset)));
        const numeric=Number(number.replace(/,/g,''));
        const span=document.createElement('span');
        span.className=numeric>0?'value-up':(numeric<0?'value-down':'');
        span.textContent=number+unit;
        frag.appendChild(span);
        last=offset+full.length;
        return full;
      });
      frag.appendChild(document.createTextNode(text.slice(last)));
      node.parentNode.replaceChild(frag,node);
    });
  }
  function signClass(value){
    const n=Number(value);
    return Number.isFinite(n)?(n>0?'up':n<0?'down':''):'';
  }

  const VIEW_BREADCRUMBS={
    data:{title:'数据看板',views:{macro:'宏观',global_markets:'全球市场',sw_industries:'一级行业',commodities:'大宗商品',stock:'个股',news_events:'新闻事件'}},
    allocation:{title:'资产配置',views:{home:'主页',cycle:'周期跟踪',strategy:'配置策略',backtest:'回测检验'}},
    rotation:{title:'行业轮动',views:{home:'主页',industry:'行业景气度',style:'风格轮动周期',allocation:'配置策略',backtest:'策略回测'}},
    liquidity:{title:'资金面跟踪',views:{home:'主页',retail:'散户资金',public:'公募基金',etf:'ETF资金',margin:'融资资金',primary:'一级市场',private:'私募基金',foreign:'外资资金'}},
    kline:{title:'K线记忆学习',views:{home:'主页',learn:'学习记忆',backtest:'策略回测',history:'历史记录'}},
    factor:{title:'LLM因子挖掘',views:{home:'主页',expression:'因子表达式',report:'因子检验结果',score:'综合打分',memory:'历史记忆'}}
  };
  function viewBreadcrumb(key){
    const parts=String(key||'data:macro').split(':'),group=VIEW_BREADCRUMBS[parts[0]]||VIEW_BREADCRUMBS.data;
    return group.title+' > '+(group.views[parts[1]]||parts[1]||'主页')+' >';
  }
  function header(title,sub,eye){
    const breadcrumb=viewBreadcrumb(S.active);
    setText('page-title',cnText(title||'--'));
    setText('page-subtitle','');
    setText('page-eyebrow',breadcrumb);
    VIEW_META.set(S.active,Object.assign({},VIEW_META.get(S.active)||{},{
      title:cnText(title||'--'),subtitle:'',eye:breadcrumb
    }));
  }
  function conclusion(html){
    const box=$('core-conclusion');
    if(!box) return;
    box.hidden=false;
    box.innerHTML='<span class="eyebrow">核心结论</span><p>'+html+'</p>';
    localizeTree(box);
    colorizeSigned(box.querySelector('p'));
    colorizeUnsignedMetrics(box.querySelector('p'));
    VIEW_META.set(S.active,Object.assign({},VIEW_META.get(S.active)||{},{conclusion:box.innerHTML,conclusionHidden:false}));
  }
  function clearConclusion(){
    const box=$('core-conclusion');
    if(!box) return;
    box.innerHTML='';
    box.hidden=true;
    VIEW_META.set(S.active,Object.assign({},VIEW_META.get(S.active)||{},{conclusion:'',conclusionHidden:true}));
  }
  function panel(id,title,sub,wide){
    return '<section class="chart-panel '+(wide?'wide':'')+'"><div class="panel-header"><div><h3>'+esc(cnText(title))+'</h3></div></div><div id="'+esc(id)+'" class="plot-frame"></div></section>';
  }
  function cardHTML(items){
    return '<div class="kpi-grid">'+arr(items).map(function(item){
      const series=item.series||item;
      const point=item.value!==undefined?{value:item.value,date:item.as_of}:latest(series);
      const before=item.value!==undefined?null:prev(series);
      const change=item.change!==undefined?item.change:(point&&before?point.value-before.value:null);
      return '<article class="kpi-card"><small>'+esc(cnText(item.label||series.label||series.name||'指标'))+
        '</small><strong>'+fmt(point&&point.value,2)+' '+esc(cnText(item.unit||series.unit||''))+
        '</strong><em><span class="'+signClass(change)+'">'+(change===null||change===undefined?TXT.latest:signed(change))+
        '</span><span>'+esc((point&&point.date)||item.as_of||series.as_of||'')+'</span></em></article>';
    }).join('')+'</div>';
  }
  function tableHTML(title,rows,cols){
    rows=arr(rows).slice(0,120);
    cols=cols&&cols.length?cols:Object.keys(obj(rows[0])).slice(0,10);
    cols=cols.filter(function(col){return !['source','provider','reference'].includes(String(col).toLowerCase());});
    return '<section class="table-panel"><div class="panel-header"><div><h3>'+esc(cnText(title))+
      '</h3></div></div><div class="table-scroll"><table class="data-table"><thead><tr>'+
      cols.map(function(c){return '<th>'+esc(fieldLabel(c))+'</th>';}).join('')+
      '</tr></thead><tbody>'+rows.map(function(row){return '<tr>'+cols.map(function(c){return cell(row,c);}).join('')+'</tr>';}).join('')+
      '</tbody></table></div></section>';
  }
  function control(id,label,rows,key,selected,applyId,resetId){
    const chosen=new Set(arr(selected).map(String));
    const opts=arr(rows).map(function(row){
      const value=String(obj(row)[key]??'');
      if(!value) return '';
      const extra=key==='industry'&&row.code?' ('+esc(row.code)+')':'';
      const labelText=key==='symbol'?commodityCN(value):cnText(value);
      return '<option value="'+esc(value)+'" '+(chosen.has(value)?'selected':'')+'>'+esc(labelText)+extra+'</option>';
    }).join('');
    return '<section class="control-card"><div class="control-grid"><label style="grid-column:span 3;">'+esc(cnText(label))+
      '<select id="'+esc(id)+'" multiple size="8">'+opts+'</select></label><button id="'+esc(applyId)+
      '" class="action-button" type="button">'+TXT.update+'</button><button id="'+esc(resetId)+
      '" class="ghost-button" type="button">'+TXT.coreGroup+'</button></div></section>';
  }

  function touchView(key){ VIEW_TOUCH.set(key,Date.now()); }
  function dropView(key){
    const pane=VIEW_CACHE.get(key);
    if(!pane) return;
    for(let i=PLOT_QUEUE.length-1;i>=0;i--){ if(PLOT_QUEUE[i].element&&pane.contains(PLOT_QUEUE[i].element)) PLOT_QUEUE.splice(i,1); }
    pane.querySelectorAll('.js-plotly-plot').forEach(function(node){try{if(window.Plotly)Plotly.purge(node);}catch(_){}});
    if(pane.parentNode) pane.parentNode.removeChild(pane);
    VIEW_CACHE.delete(key);
    VIEW_META.delete(key);
    VIEW_TOUCH.delete(key);
  }
  function pruneViews(keepKey){
    const candidates=Array.from(VIEW_CACHE.keys()).filter(function(key){return key!==keepKey;})
      .sort(function(a,b){return (VIEW_TOUCH.get(a)||0)-(VIEW_TOUCH.get(b)||0);});
    while(VIEW_CACHE.size>VIEW_CACHE_LIMIT&&candidates.length) dropView(candidates.shift());
  }
  function ensurePane(key){
    const host=$('view-root');
    if(!host) return null;
    const mounted=host.firstElementChild;
    if(displayedView===key&&mounted&&mounted.dataset.view===key) return mounted;
    if(displayedView&&mounted&&mounted.dataset.view===displayedView) VIEW_CACHE.set(displayedView,mounted);
    let pane=VIEW_CACHE.get(key);
    if(!pane){
      pane=document.createElement('div');
      pane.className='view-cache-pane';
      pane.dataset.view=key;
      pane.innerHTML='<div class="loading-card">正在准备页面与图表。</div>';
      VIEW_CACHE.set(key,pane);
    }
    host.replaceChildren(pane);
    displayedView=key;
    touchView(key);
    return pane;
  }
  function restoreMeta(key){
    const meta=VIEW_META.get(key);
    if(!meta) return;
    setText('page-title',meta.title||'--');
    setText('page-subtitle',meta.subtitle||'');
    setText('page-eyebrow',meta.eye||'研究总览');
    const box=$('core-conclusion');
    if(box&&meta.conclusion!==undefined){
      box.innerHTML=meta.conclusion;
      box.hidden=!!meta.conclusionHidden;
    }
  }
  function showCachedView(key){
    const pane=VIEW_CACHE.get(key);
    if(!pane||pane.dataset.ready!=='1'||pane.dataset.view!==key){
      if(pane) VIEW_CACHE.delete(key);
      return false;
    }
    const host=$('view-root');
    if(displayedView&&host.firstElementChild) VIEW_CACHE.set(displayedView,host.firstElementChild);
    host.replaceChildren(pane);
    displayedView=key;
    touchView(key);
    restoreMeta(key);
    requestAnimationFrame(function(){
      pane.querySelectorAll('.js-plotly-plot').forEach(function(plotNode){
        try{ if(window.Plotly&&Plotly.Plots) Plotly.Plots.resize(plotNode); }catch(_){}
      });
      schedulePlotDrain();
    });
    return true;
  }
  function root(html){
    const pane=ensurePane(S.active);
    if(!pane) return;
    pane.dataset.view=S.active;
    pane.innerHTML=html;
    pane.dataset.ready='1';
    localizeTree(pane);
    pane.querySelectorAll('.section-heading p').forEach(function(node){colorizeSigned(node);colorizeUnsignedMetrics(node);});
    VIEW_CACHE.set(S.active,pane);
    touchView(S.active);
    pruneViews(S.active);
  }
  function invalidateView(key){
    dropView(key);
    if(displayedView===key) displayedView=null;
  }
  async function render(force){
    seq=0;
    if(!force&&showCachedView(S.active)) return;
    ensurePane(S.active);
    const parts=S.active.split(':');
    if(parts[0]==='data') return await renderData(parts[1]);
    if(parts[0]==='kline') return await renderKline(parts[1]);
    if(parts[0]==='factor') return await renderFactor(parts[1]);
  }

  function styleTrace(trace,index){
    const item=Object.assign({},trace);
    const type=String(item.type||'scatter').toLowerCase();
    item.name=cnText(item.name||'');
    if(Array.isArray(item.text)) item.text=item.text.map(function(x){return cnText(commodityCN(x));});
    if(Array.isArray(item.x)) item.x=item.x.map(function(x){
      const raw=String(x);
      return COMMODITY_CN[raw.toUpperCase()]||MARKET_CN_R9[raw]||x;
    });
    if(type==='candlestick'){
      item.increasing={line:{color:'#c00000'},fillcolor:'#c00000'};
      item.decreasing={line:{color:'#00b050'},fillcolor:'#00b050'};
      return item;
    }
    if(type==='heatmap'){
      item.colorscale=[[0,'#00b050'],[0.5,'#ffffff'],[1,'#c00000']];
      return item;
    }
    if(type==='bar'){
      item.marker=Object.assign({},item.marker||{});
      if(!item.marker.color) item.marker.color=CHART_PALETTE[index%CHART_PALETTE.length];
      return item;
    }
    const semantic=/买入|加仓/.test(item.name)?'#c00000':(/卖出|减仓/.test(item.name)?'#00b050':CHART_PALETTE[index%CHART_PALETTE.length]);
    item.line=Object.assign({},item.line||{},{color:semantic,width:(item.line&&item.line.width)||2});
    item.marker=Object.assign({},item.marker||{});
    if(item.marker.color===undefined||typeof item.marker.color==='string') item.marker.color=semantic;
    return item;
  }
  function localizedLayout(layout){
    const out=Object.assign({},layout||{});
    ['xaxis','yaxis','yaxis2','xaxis2'].forEach(function(axisName){
      if(!out[axisName]) return;
      out[axisName]=Object.assign({},out[axisName]);
      if(typeof out[axisName].title==='string') out[axisName].title=cnText(out[axisName].title);
      if(out[axisName].title&&typeof out[axisName].title.text==='string') out[axisName].title=Object.assign({},out[axisName].title,{text:cnText(out[axisName].title.text)});
      if(!out[axisName].gridcolor) out[axisName].gridcolor='rgba(191,191,191,.55)';
      if(!out[axisName].zerolinecolor) out[axisName].zerolinecolor='rgba(128,128,128,.55)';
    });
    out.legend=Object.assign({orientation:'h',y:-0.22},out.legend||{}); out.legend.font=Object.assign({},out.legend.font||{},{size:12});
    return out;
  }
  function schedulePlotDrain(){
    if(plotRunning||!PLOT_QUEUE.length) return;
    requestAnimationFrame(drainPlotQueue);
  }
  function drainPlotQueue(){
    if(plotRunning||!PLOT_QUEUE.length) return;
    let index=PLOT_QUEUE.findIndex(function(task){return task.element&&task.element.isConnected;});
    if(index<0) return;
    const task=PLOT_QUEUE.splice(index,1)[0];
    if(task.element.dataset.plotToken!==task.token){schedulePlotDrain();return;}
    plotRunning=true;
    let result;
    try{
      task.element.replaceChildren();
      result=window.Plotly.react(task.element,task.traces,task.layout,{responsive:true,displayModeBar:false,staticPlot:false});
    }catch(error){
      task.element.innerHTML='<div class="chart-fallback">图表生成失败</div>';
      result=Promise.resolve();
    }
    Promise.resolve(result).catch(function(){
      task.element.innerHTML='<div class="chart-fallback">图表生成失败</div>';
    }).finally(function(){plotRunning=false;requestAnimationFrame(schedulePlotDrain);});
  }
  function traceXAxisKey(trace){const ref=String((trace&&trace.xaxis)||'x');return ref==='x'?'xaxis':'xaxis'+ref.slice(1);}
  function axisDate(value){const text=String(value==null?'':value);if(!/^\d{4}[-/]\d{1,2}([-/]\d{1,2})?/.test(text))return null;const date=new Date(text.replace(/\//g,'-'));return Number.isNaN(date.getTime())?null:date;}
  function applyXAxisLabelPolicy(element,traces,layout){
    const out=Object.assign({},layout||{}),keys=Object.keys(out).filter(function(key){return /^xaxis\d*$/.test(key);});
    arr(traces).forEach(function(trace){const key=traceXAxisKey(trace);if(!keys.includes(key))keys.push(key);});
    if(!keys.length)keys.push('xaxis');let vertical=false;
    keys.forEach(function(key){
      const axis=Object.assign({},out[key]||{});if(axis.visible===false){out[key]=axis;return;}
      const values=[],axisTraces=arr(traces).filter(function(trace){return traceXAxisKey(trace)===key;});
      axisTraces.forEach(function(trace){arr(trace&&trace.x).forEach(function(value){if(value!==null&&value!==undefined&&value!=='')values.push(value);});});
      const manual=arr(axis.tickvals).length?arr(axis.tickvals):null,labels=manual||Array.from(new Set(values.map(String))),numeric=labels.length&&labels.filter(function(value){return Number.isFinite(Number(value));}).length/labels.length>.8;
      const dates=labels.map(axisDate).filter(Boolean),dateLike=labels.length&&dates.length/labels.length>.8;
      let count=6,labelWidth=58;
      if(dateLike){
        const sorted=dates.slice().sort(function(a,b){return a-b;}),months=sorted.length>1?Math.max(1,(sorted[sorted.length-1].getFullYear()-sorted[0].getFullYear())*12+sorted[sorted.length-1].getMonth()-sorted[0].getMonth()):1,long=months>=24;
        if(axis.type!=='category'){axis.type='date';if(!axis.dtick)axis.dtick=long?'M6':'M1';}
        axis.tickformat='%Y-%m';if(manual)axis.ticktext=manual.map(function(value){const match=String(value).match(/^(\d{4})[-/](\d{1,2})/);return match?match[1]+'-'+String(match[2]).padStart(2,'0'):String(value);});
        if(manual)count=manual.length;else{const step=String(axis.dtick||'').toUpperCase()==='M6'?6:String(axis.dtick||'').toUpperCase()==='M3'?3:1;count=Math.floor(months/step)+1;}
        labelWidth=82;
      }else if(!numeric){
        count=labels.length;labelWidth=Math.min(150,Math.max(36,labels.reduce(function(max,value){return Math.max(max,String(value).length);},0)*12));
      }
      const domain=arr(axis.domain),domainRatio=domain.length===2?Math.max(.2,Number(domain[1])-Number(domain[0])):1,margin=Object.assign({l:48,r:20},out.margin||{}),usable=Math.max(220,(element.clientWidth||900)-Number(margin.l||0)-Number(margin.r||0))*domainRatio,horizontal=count*labelWidth<=usable*.94;
      axis.tickangle=horizontal?0:-90;axis.automargin=true;axis.ticklabelposition='outside';axis.tickfont=Object.assign({},axis.tickfont||{},{size:horizontal?12:9});out[key]=axis;
      if(!horizontal)vertical=true;
    });
    if(vertical){out.margin=Object.assign({},out.margin||{});out.margin.b=Math.max(Number(out.margin.b||48),126);if(out.legend&&out.legend.orientation==='h')out.legend=Object.assign({},out.legend,{y:Math.min(Number(out.legend.y??-.2),-.42)});}
    return out;
  }
  function plot(id,traces,layout){
    const element=$(id);
    if(!element) return;
    const visible=arr(traces).slice(0,12);
    if(!window.Plotly||!visible.length){
      element.innerHTML='<div class="chart-fallback">'+TXT.noData+'</div>';
      return;
    }
    const token=String(Date.now())+'-'+String(Math.random()).slice(2);
    element.dataset.plotToken=token;
    element.innerHTML='<div class="chart-loading">\u6b63\u5728\u751f\u6210\u56fe\u8868</div>';
    const base={font:{family:'KaiTi,STKaiti,"Kaiti SC","Microsoft YaHei",serif',size:12,color:'#111827'},paper_bgcolor:'#ffffff',plot_bgcolor:'#ffffff',margin:{l:48,r:20,t:14,b:48},hoverlabel:{font:{family:'KaiTi,STKaiti,\"Kaiti SC\",\"Microsoft YaHei\",serif',size:13}},colorway:CHART_PALETTE};
    const styled=visible.map(styleTrace),localized=localizedLayout(Object.assign(base,layout||{})),fixed=applyXAxisLabelPolicy(element,styled,localized);
    PLOT_QUEUE.push({element:element,token:token,traces:styled,layout:fixed});
    schedulePlotDrain();
  }
  function line(id,list,opt){
    opt=Object.assign({max:220,rebase:false},opt||{});
    const traces=arr(list).slice(0,6).map(function(series,index){
      let points=pts(series,opt.max);
      if(opt.rebase&&points.length){
        const base=points.find(function(point){return point.value!==0;});
        if(base) points=points.map(function(point){return {date:point.date,value:point.value/base.value*100};});
      }
      return {type:'scatter',mode:'lines',connectgaps:true,name:seriesLabel(series),x:points.map(function(p){return p.date;}),y:points.map(function(p){return p.value;}),line:{width:2,color:CHART_PALETTE[index]}};
    }).filter(function(trace){return trace.x.length>=1;});
    plot(id,traces,{hovermode:'x unified',legend:{orientation:'h',y:-0.22,font:{size:12}},yaxis:{gridcolor:'rgba(191,191,191,.55)'},xaxis:{showgrid:false,type:'date',tickformat:'%Y年%m月'}});
  }
  function lineSmart(id,list,opt){
    opt=Object.assign({max:220,rebase:false},opt||{});
    const units=Array.from(new Set(arr(list).map(function(s){return s.unit||'';}).filter(Boolean)));
    const primary=units[0]||'';
    const traces=arr(list).slice(0,6).map(function(series,index){
      let points=pts(series,opt.max);
      if(opt.rebase&&points.length){
        const base=points.find(function(point){return point.value!==0;});
        if(base) points=points.map(function(point){return {date:point.date,value:point.value/base.value*100};});
      }
      const right=!opt.rebase&&units.length>1&&series.unit!==primary;
      const unit=cnText(series.unit||'');
      return {type:'scatter',mode:'lines',connectgaps:true,name:seriesLabel(series)+(unit?' - '+unit:'')+(right?' - 右轴':''),
        x:points.map(function(p){return p.date;}),y:points.map(function(p){return p.value;}),yaxis:right?'y2':'y',
        line:{width:2,color:CHART_PALETTE[index]}};
    }).filter(function(trace){return trace.x.length>=1;});
    const layout={hovermode:'x unified',legend:{orientation:'h',y:-0.24,font:{size:12}},yaxis:{title:cnText(primary),gridcolor:'rgba(191,191,191,.55)'},xaxis:{showgrid:false,type:'date',tickformat:'%Y年%m月'}};
    if(units.length>1&&!opt.rebase) layout.yaxis2={title:units.filter(function(u){return u!==primary;}).map(cnText).join('/'),overlaying:'y',side:'right',showgrid:false,zeroline:false};
    plot(id,traces,layout);
  }
  function bar(id,rows){
    const data=arr(rows).filter(function(row){return Number.isFinite(Number(row.value));});
    plot(id,data.length?[{type:'bar',x:data.map(function(row){return commodityCN(cnText(row.label));}),y:data.map(function(row){return Number(row.value);}),
      marker:{color:data.map(function(row){return Number(row.value)>=0?'#c00000':'#00b050';})},
      text:data.map(function(row){return fmt(row.value);}),textposition:'auto'}]:[],
      {showlegend:false,xaxis:{tickangle:-25,showgrid:false},yaxis:{gridcolor:'rgba(191,191,191,.55)'}});
  }
  function scatter(id,rows){
    const data=arr(rows).filter(function(row){return Number.isFinite(Number(row.x))&&Number.isFinite(Number(row.y));});
    plot(id,data.length?[{type:'scatter',mode:'markers+text',x:data.map(function(row){return +row.x;}),y:data.map(function(row){return +row.y;}),
      text:data.map(function(row){return commodityCN(cnText(row.label));}),textposition:'top center',
      marker:{size:10,color:'#c00000',opacity:.78}}]:[],
      {showlegend:false,xaxis:{gridcolor:'rgba(191,191,191,.55)'},yaxis:{gridcolor:'rgba(191,191,191,.55)'}});
  }

  async function api(path,opt){
    const options=Object.assign({credentials:'same-origin'},opt||{});
    const method=String(options.method||'GET').toUpperCase();
    const cacheable=method==='GET'&&!/\/api\/(services|ai\/|kline\/job\/|factor\/job\/)/.test(path);
    const key=cacheable?apiPath(path):'';
    const now=Date.now();
    const hit=key&&API_MEMO.get(key);
    if(hit&&now-hit.at<600000) return hit.promise;
    const promise=fetch(apiPath(path),options).then(async function(response){
      const text=await response.text();
      let payload={};
      try{payload=text?JSON.parse(text):{};}catch(_){payload={raw:text};}
      if(!response.ok) throw new Error(payload.message||payload.error||payload.data_state||('请求失败 '+response.status));
      return payload;
    });
    if(key) API_MEMO.set(key,{at:now,promise:promise});
    try{return await promise;}catch(error){if(key)API_MEMO.delete(key);throw error;}
  }
  async function fetchSeries(ids){
    ids=Array.from(new Set(arr(ids).filter(Boolean)));
    const missing=ids.filter(function(id){return !S.seriesCache[id];});
    const batches=[];
    for(let i=0;i<missing.length;i+=24) batches.push(missing.slice(i,i+24));
    let cursor=0;
    const workers=Array.from({length:Math.min(4,batches.length)},async function(){
      while(cursor<batches.length){
        const batch=batches[cursor++];
        try{
          const payload=await api('/api/board/series?ids='+encodeURIComponent(batch.join(','))+'&frequency=raw');
          arr(payload.series).forEach(function(series){series.points=series.data||series.points||[];S.seriesCache[series.id]=series;});
        }catch(error){
          batch.forEach(function(id){if(!S.seriesCache[id])S.seriesCache[id]={id:id,status:'failed',data:[]};});
        }
      }
    });
    await Promise.allSettled(workers);
    return ids.map(function(id){return S.seriesCache[id];}).filter(Boolean);
  }
  function warmCaches(){
    if(prefetchStarted)return;
    prefetchStarted=true;
    // K-line and factor metadata are small. Warm their authenticated proxy chains
    // while the user reads the home brief; keep multi-megabyte snapshots on demand.
    const warmMetadata=async function(){
      await Promise.allSettled([
        api('/api/factor/status'),
        api('/api/factor/history'),
        api('/api/kline/health'),
        api('/api/kline/history?limit=80').then(function(payload){
          const latest=arr(payload.history)[0],tasks=[
            api('/api/kline/stocks?limit=80&q=000001'),
            api('/api/kline/dates?code=000001')
          ];
          if(latest&&latest.job_id)tasks.push(api('/api/kline/jobs/'+encodeURIComponent(latest.job_id)));
          return Promise.allSettled(tasks);
        })
      ]);
    };
    if('requestIdleCallback' in window)window.requestIdleCallback(function(){warmMetadata();},{timeout:1500});
    else window.setTimeout(warmMetadata,400);
  }
  function loadServices(){
    return api('/api/services').then(function(payload){S.services=payload;serviceBadges();}).catch(function(){
      const badges=$('service-badges');
      if(badges) badges.innerHTML='<span class="service-badge failed">服务异常</span>';
    });
  }
  async function init(){
    bindNav();
    tick();
    setInterval(tick,30000);
    loadServices();
    const cached=readSnapshotCache();
    if(cached){
      S.snapshot=cached;
      await render().catch(function(error){conclusion('缓存渲染失败：'+esc(error.message));});
      warmCaches();
    }
    try{
      await loadSnapshot();
      writeSnapshotCache();
      if(!cached) await render();
      warmCaches();
    }catch(error){
      if(!cached) conclusion('数据加载失败：'+esc(error.message));
    }
    loadPlotly().then(function(ready){
      if(!ready) return;
      const redraw=function(){
        if(navBusy){window.setTimeout(redraw,120);return;}
        render(true).catch(function(error){console.error("图表重绘异常",error);});
      };
      if("requestIdleCallback" in window) window.requestIdleCallback(redraw,{timeout:600});
      else window.setTimeout(redraw,0);
    });
    setInterval(loadServices,300000);
  }
  async function requestNav(target){
    if(!target) return;
    if(workspaceControlPending){
      try{await workspaceControlQueue;}catch(_){/* the queue reports its own error */}
    }
    if(navBusy){
      if(target!==S.active) pendingNavTarget=target;
      return;
    }
    if(target===S.active) return;
    navBusy=true;
    let next=target;
    try{
      while(next){
        pendingNavTarget=null;
        const current=next;
        const button=Array.from(document.querySelectorAll('.nav-item')).find(function(item){return item.dataset.target===current;});
        if(!button) break;
        button.classList.add('is-loading');
        button.dataset.status='running';
        if(button.closest('.nav-group'))button.closest('.nav-group').dataset.status='running';
        document.querySelectorAll('.nav-item').forEach(function(item){item.classList.toggle('is-active',item===button);});
        S.active=current;
        window.scrollTo({top:0,left:0,behavior:'auto'});
        try{await render();}catch(error){console.error('页面渲染异常',error&&error.stack?error.stack:error);conclusion('页面加载失败：'+esc(error.message));}
        finally{button.classList.remove('is-loading');applyNavStatuses();}
        next=pendingNavTarget&&pendingNavTarget!==S.active?pendingNavTarget:null;
      }
    }finally{
      pendingNavTarget=null;
      navBusy=false;
    }
  }
  function bindNav(){
    document.querySelectorAll('.nav-group-toggle').forEach(function(button){
      if(button.dataset.boundR39) return;
      button.dataset.boundR39='1';
      button.addEventListener('click',function(event){
        if(!event.isTrusted) return;
        const expanded=button.getAttribute('aria-expanded')==='true';
        const children=button.parentElement&&button.parentElement.querySelector('.nav-children');
        button.setAttribute('aria-expanded',expanded?'false':'true');
        if(children) children.hidden=expanded;
      });
    });
    document.querySelectorAll('.nav-item').forEach(function(button){
      if(button.dataset.boundR9) return;
      button.dataset.boundR9='1';
      button.addEventListener('click',function(event){
        if(!event.isTrusted) return;
        event.preventDefault();
        requestNav(button.dataset.target);
      });
    });
  }
  async function renderFactor(view){
    const heading=HEAD['factor:'+view]||HEAD['factor:home'];
    header(heading[0],heading[1],'LLM因子挖掘');
    if(view==='home') return await factorHome();
    if(view==='memory') return await factorMemory();
    await factorDetail();
    if(view==='expression') return factorExpression();
    if(view==='report') return factorReport();
    if(view==='score') return factorScore();
  }
  async function klineHistory(){
    await needKline();
    clearConclusion();
    const rows=S.kline.history.map(function(row){return Object.assign({view:'查看'},row);});
    root(tableHTML('K线历史记录',rows,['created_at','code','as_of','status','analysis_depth','holding_days','test_return','max_drawdown','view','job_id']));
    document.querySelectorAll('[data-kline-view]').forEach(function(button){
      button.onclick=async function(event){
        event.preventDefault();
        if(!event.isTrusted||navBusy) return;
        navBusy=true;
        try{
          await loadKlineJob(button.dataset.klineView);
          invalidateView('kline:learn');
          invalidateView('kline:backtest');
          S.active='kline:learn';
          document.querySelectorAll('.nav-item').forEach(function(item){item.classList.toggle('is-active',item.dataset.target===S.active);});
          window.scrollTo({top:0,left:0,behavior:'auto'});
          await render();
        }finally{navBusy=false;}
      };
    });
  }
  async function factorMemory(){
    await needFactor();
    const rows=fRows().map(function(row){return Object.assign({factor_view:'查看'},row);});
    conclusion('因子历史记忆 '+rows.length+' 条记录，点击查看会同步刷新表达式、检验报告和综合打分页。');
    root(tableHTML('因子历史记忆',rows,['factor_view','source','job_id','created_at','universe','status','target_accepted','candidate_count','accepted_count','elapsed_seconds']));
    document.querySelectorAll('[data-factor-view],[data-job-id]').forEach(function(button){
      button.onclick=async function(event){
        event.preventDefault();
        if(!event.isTrusted||navBusy) return;
        navBusy=true;
        try{
          const id=button.dataset.factorView||button.dataset.jobId;
          await factorDetail(id);
          ['factor:expression','factor:report','factor:score'].forEach(invalidateView);
          S.active='factor:expression';
          document.querySelectorAll('.nav-item').forEach(function(item){item.classList.toggle('is-active',item.dataset.target===S.active);});
          window.scrollTo({top:0,left:0,behavior:'auto'});
          await render();
        }finally{navBusy=false;}
      };
    });
  }
  /* K-line Agent 5.2: final controls and ranked memory presentation. */
  function klineControls(){
    var html = '<section class="control-card"><div class="control-grid">';
    html += '<label style="grid-column:span 2;">\u80a1\u7968\u641c\u7d22<input id="kq" value="000001"></label>';
    html += '<button id="ks" class="ghost-button" type="button">' + TXT.search + '</button>';
    html += '<label style="grid-column:span 2;">\u80a1\u7968<select id="kst"></select></label>';
    html += '<label>\u622a\u6b62\u65e5\u671f<select id="kd"><option value="latest">\u6700\u65b0\u53ef\u7528</option></select></label>';
    html += '<label>\u5206\u6790\u6df1\u5ea6<select id="kdepth">';
    html += '<option value="fast">\u5feb\u901f\uff1a\u672c\u5730\u8bb0\u5fc6\u5b66\u4e60</option>';
    html += '<option value="standard">\u6807\u51c6\uff1aGPT\u89c4\u5219\u590d\u6838</option>';
    html += '<option value="deep">\u6df1\u5ea6\uff1a\u591a\u8f6e\u6539\u5199\u4e0e\u9a8c\u8bc1</option>';
    html += '</select></label>';
    html += '<label>\u6301\u6709\u7a97\u53e3<select id="kh">';
    html += '<option value="20">20\u65e5\uff08\u9ed8\u8ba4\uff09</option><option value="5">5\u65e5</option><option value="10">10\u65e5</option><option value="60">60\u65e5</option>';
    html += '</select></label>';
    html += '<label>\u4ed3\u4f4d\u6863\u4f4d<select id="kp">';
    html += '<option value="balanced">\u5e73\u8861\uff1a0/25/50/75/100%</option>';
    html += '<option value="conservative">\u7a33\u5065\uff1a0/25/50/75/100%</option>';
    html += '<option value="aggressive">\u654f\u6377\uff1a0/25/50/75/100%</option>';
    html += '</select></label>';
    html += '<label>同类学习<select id="kcohort">';
    html += '<option value="hybrid">行业优先 + 风格补足</option>';
    html += '<option value="industry">仅同行业</option>';
    html += '<option value="style">仅同风格</option>';
    html += '<option value="individual">仅个股</option>';
    html += '</select></label>';
    html += '<button id="kstart" class="action-button" type="button">开始学习</button>';
    html += '</div></section>';
    return html;
  }
  function kMemoryRows(j){
    var sum = obj(j.summary);
    var cats = obj(sum.learning_categories);
    var rows = [];
    Object.keys(cats).forEach(function(category){
      arr(cats[category]).forEach(function(rule){
        rows.push(Object.assign({category:category}, rule));
      });
    });
    return rows.sort(function(a,b){
      var ad = obj(a.learning_diagnostics);
      var bd = obj(b.learning_diagnostics);
      var as = Number(a.accuracy_score == null ? ad.accuracy_score || 0 : a.accuracy_score);
      var bs = Number(b.accuracy_score == null ? bd.accuracy_score || 0 : b.accuracy_score);
      return bs - as || Number(b.confidence || 0) - Number(a.confidence || 0);
    });
  }
  function kRuleDirectionText(direction){
    if(Number(direction) > 0) return '\u4e70\u5165/\u52a0\u4ed3\u5019\u9009';
    if(Number(direction) < 0) return '\u51cf\u4ed3/\u5356\u51fa\u5019\u9009';
    return '\u89c2\u5bdf\u5019\u9009';
  }
  function kRuleSentence(rule){
    var condition = String(rule.applicable_conditions || '').replace(/\s+/g,' ').trim();
    var name = String(rule.name_cn || rule.rule_id || '');
    if(!condition) condition = name + '\u51fa\u73b0';
    var diagnostics = obj(rule.learning_diagnostics);
    var score = Number(rule.accuracy_score == null ? diagnostics.accuracy_score || 0 : rule.accuracy_score);
    return freqText(rule.frequency || '--') + '\u4e2d\uff0c' + condition + '\u65f6\u89e6\u53d1\u201c' +
      name + '\u201d\uff0c\u5b66\u4e60\u540e\u4f5c\u4e3a' + kRuleDirectionText(rule.direction) +
      '\uff1b\u8be5\u80a1\u8bad\u7ec3/\u9a8c\u8bc1\u51c6\u5ea6\u5f97\u5206' + fmt(score,1) +
      '\u5206\uff0c\u5f53\u524d\u72b6\u6001\u4e3a' + statusText(rule.status || 'available') + '\u3002';
  }
  function memoryCards(rows){
    if(!rows.length) return '<div class="empty-state">\u6682\u65e0\u8bb0\u5fc6\u8bb0\u5f55\u3002</div>';
    var groups = {};
    rows.forEach(function(rule){
      var category = rule.category || rule.family_cn || '\u5176\u4ed6\u89c4\u5219';
      if(!groups[category]) groups[category] = [];
      groups[category].push(rule);
    });
    var html = '<div class="memory-groups">';
    Object.keys(groups).forEach(function(category, groupIndex){
      var rules = groups[category];
      html += '<details class="memory-group"' + (groupIndex === 0 ? ' open' : '') + '>';
      html += '<summary><strong>' + esc(category) + '</strong><span>' + rules.length + '\u6761\uff0c\u6309\u51c6\u5ea6\u4ece\u9ad8\u5230\u4f4e</span></summary>';
      html += '<div class="memory-list">';
      rules.forEach(function(rule, index){
        var diagnostics = obj(rule.learning_diagnostics);
        var score = Number(rule.accuracy_score == null ? diagnostics.accuracy_score || 0 : rule.accuracy_score);
        var rank = Number(rule.category_rank || index + 1);
        html += '<article class="memory-row">';
        html += '<span class="memory-rank">#' + rank + '<b class="pill ' + esc(rule.status || '') + '">' + esc(statusText(rule.status || 'available')) + '</b></span>';
        html += '<strong>' + esc(freqText(rule.frequency || '--')) + ' | ' + esc(rule.name_cn || rule.rule_id || '') + '</strong>';
        html += '<em>\u51c6\u5ea6 ' + esc(fmt(score,1)) + '\u5206</em>';
        html += '<p>' + esc(kRuleSentence(rule)) + '</p>';
        html += '</article>';
      });
      html += '</div></details>';
    });
    html += '</div>';
    return html;
  }
  Object.assign(COL,{
    ts_code:'股票代码',stock_name:'股名',industry_name:'行业',
    same_industry:'同行业',similarity:'相似度',board:'板块',
    volatility:'波动率',trend_120:'120日趋势',decision:'记忆操作',
    rule_id:'规则',frequency:'频率',cross_stock_score:'跨股得分'
  });
  function kStockLabels(labels){
    var map=[['industry_name','行业'],['board','板块'],['liquidity_style','流动性'],['volatility_style','波动'],['trend_style','趋势'],['listing_history_bars','历史K线']];
    return '<div class="stock-labels">'+map.map(function(item){
      var value=labels[item[0]];
      return value===undefined||value===null||value===''?'':'<span><b>'+esc(item[1])+'</b>'+esc(value)+'</span>';
    }).join('')+'</div>';
  }
  function kPeerRows(report){
    return arr(report.peer_cards).map(function(peer){
      return {
        ts_code:peer.ts_code,stock_name:peer.stock_name||'--',industry_name:peer.industry_name||'--',
        same_industry:peer.same_industry?'是':'否',similarity:fmt(Number(peer.similarity||0)*100,1)+'%',
        board:peer.board||'--',volatility:fmt(Number(peer.volatility||0)*100,1)+'%',
        trend_120:fmt(Number(peer.trend_120||0)*100,1)+'%'
      };
    });
  }
  function kContextMemory(memory){
    var notes=arr(memory.notes);
    if(!notes.length) return '<div class="empty-state">尚无已保存的情境记忆。</div>';
    return '<div class="memory-list">'+notes.map(function(note){
      return '<article class="memory-row"><span class="pill '+esc(note.status||'')+'">'+esc(statusText(note.status||'available'))+'</span>'+
        '<strong>'+esc(freqText(note.frequency||'--'))+' | '+esc(note.name_cn||note.rule_id||'')+'</strong>'+
        '<em>跨股 '+esc(fmt(Number(note.cross_stock_score||0)*100,1))+'%</em>'+
        '<p>'+esc(note.situation||'')+'；'+esc(note.experience_summary||note.suggested_adjustment||'')+'</p></article>';
    }).join('')+'</div>';
  }
  function kEvolution(report,fiveState){
    var candidates=arr(report.candidates);
    var rows=candidates.map(function(row){
      return '<article class="evolution-row'+(row.candidate_id===report.selected_candidate_id?' is-selected':'')+'">'+
        '<strong>'+esc(row.name_cn||row.candidate_id||'')+'</strong><span>入选 '+esc(row.accepted_rule_count||0)+' 条</span>'+
        '<em>目标值 '+esc(fmt(row.objective||0,3))+'</em></article>';
    }).join('');
    var stateStatus=fiveState.enabled?(fiveState.accepted?'已通过训练/验证门控':'未通过，保留旧冠军'):'仅深度模式运行';
    return '<div class="evolution-list">'+(rows||'<div class="empty-state">暂无进化候选。</div>')+
      '<article class="evolution-row"><strong>0/25/50/75/100% 五档仓位挑战者</strong><span>'+esc(stateStatus)+'</span></article></div>';
  }
  function bindKlineLearningPanes(){
    document.querySelectorAll('[data-kpane]').forEach(function(button){
      button.onclick=function(){
        document.querySelectorAll('[data-kpane]').forEach(function(item){item.classList.toggle('is-active',item===button);});
        document.querySelectorAll('.kline-learning-pane').forEach(function(pane){pane.hidden=pane.id!==button.dataset.kpane;});
      };
    });
  }
  async function klineLearn(){
    await needKline();
    var j=currentKlineJob();
    if(j.job_id&&!j.summary) j=await loadKlineJob(j.job_id);
    var sum=obj(j.summary),rows=kMemoryRows(j),empty=!j.job_id;
    var cohort=obj(sum.cohort_learning),labels=obj(sum.stock_labels||cohort.stock_labels);
    var context=obj(sum.context_memory),evolution=obj(sum.evolution_report),fiveState=obj(sum.five_state_position_evolution);
    clearConclusion();
    var nav='<nav class="kline-learning-nav" aria-label="学习结果分组">'+
      '<button type="button" class="is-active" data-kpane="kpane-rules">规则排名</button>'+
      '<button type="button" data-kpane="kpane-peers">同类公司</button>'+
      '<button type="button" data-kpane="kpane-memory">情境记忆</button>'+
      '<button type="button" data-kpane="kpane-evolution">进化记录</button></nav>';
    var peerRows=kPeerRows(cohort);
    var content='<section id="kpane-rules" class="kline-learning-pane">'+memoryCards(rows)+'</section>'+
      '<section id="kpane-peers" class="kline-learning-pane" hidden>'+kStockLabels(labels)+
      tableHTML('同类公司训练边界样本',peerRows,['ts_code','stock_name','industry_name','same_industry','similarity','board','volatility','trend_120'])+'</section>'+
      '<section id="kpane-memory" class="kline-learning-pane" hidden>'+kContextMemory(context)+'</section>'+
      '<section id="kpane-evolution" class="kline-learning-pane" hidden>'+kEvolution(evolution,fiveState)+'</section>';
    root((empty?klineControls():'')+'<section class="workbench-panel"><h2>学习记忆</h2>'+
      (empty?'<p class="empty-state">尚无学习任务，请在上方选择参数后开始。</p>':'')+
      jobHTML(j,'kline')+nav+content+'</section>');
    if(empty) await bindKlineControls();
    bindKlineLearningPanes();
  }
  function klineDate(value){
    return String(value || '').replace(/(\d{4})(\d{2})(\d{2})/,'$1-$2-$3');
  }
  function kSignalActionText(action){
    var text = String(action || '').toLowerCase();
    if(/buy|add/.test(text)) return '\u4e70\u5165/\u52a0\u4ed3';
    if(/sell|reduce|exit/.test(text)) return '\u51cf\u4ed3/\u5356\u51fa';
    if(/hold|keep/.test(text)) return '\u4fdd\u6301\u4ed3\u4f4d';
    return '\u4ed3\u4f4d\u8c03\u6574';
  }
  function kNodeText(node){
    var detail = String(node.annotation_text || node.trigger_summary || node.action || '').replace(/\s+/g,' ').trim();
    var parts = detail.split('\uff1b').filter(Boolean).slice(0,2);
    detail = parts.join('\uff1b');
    if(detail.length > 94) detail = detail.slice(0,92) + '\u2026';
    return detail || kSignalActionText(node.action);
  }
  function klineCandle(id,j){
    var sum = obj(j.summary);
    var daily = arr(obj(sum.chart_data).daily);
    var nodes = arr(sum.signal_nodes);
    var x = daily.map(function(row){ return klineDate(row[0]); });
    var candle = {
      type:'candlestick', x:x,
      open:daily.map(function(row){return Number(row[1]);}),
      high:daily.map(function(row){return Number(row[2]);}),
      low:daily.map(function(row){return Number(row[3]);}),
      close:daily.map(function(row){return Number(row[4]);}),
      name:'K\u7ebf',
      increasing:{line:{color:'#c00000',width:1},fillcolor:'#c00000'},
      decreasing:{line:{color:'#168a47',width:1},fillcolor:'#168a47'},
      whiskerwidth:.45,
      hovertemplate:'%{x}<br>\u5f00 %{open:.2f}<br>\u9ad8 %{high:.2f}<br>\u4f4e %{low:.2f}<br>\u6536 %{close:.2f}<extra></extra>'
    };
    var volume = {
      type:'bar', x:x, y:daily.map(function(row){return Number(row[6]);}),
      name:'\u6210\u4ea4\u91cf', yaxis:'y2',
      marker:{color:'rgba(47,117,181,.20)',line:{width:0}},
      hovertemplate:'%{x}<br>\u6210\u4ea4\u91cf %{y:.3s}<extra></extra>'
    };
    var closes = daily.map(function(row){return Number(row[4]);});
    function movingAverage(windowSize){
      var sum = 0;
      return closes.map(function(value,index){
        sum += value;
        if(index >= windowSize) sum -= closes[index-windowSize];
        return index + 1 < windowSize ? null : sum / windowSize;
      });
    }
    function maTrace(windowSize,name,color,width){
      return {
        type:'scatter',mode:'lines',connectgaps:false,name:name,x:x,
        y:movingAverage(windowSize),
        line:{color:color,width:width},hovertemplate:'%{x}<br>'+name+' %{y:.2f}<extra></extra>'
      };
    }
    function nodeDate(node){ return klineDate(node.execution_date || node.date); }
    function nodePrice(node){ return Number(node.execution_price || node.price); }
    var buys = nodes.filter(function(node){return /buy|add/i.test(String(node.action || ''));});
    var sells = nodes.filter(function(node){return /sell|reduce|exit/i.test(String(node.action || ''));});
    function markerTrace(items,name,color,symbol){
      return {
        type:'scatter',mode:'markers',name:name,
        x:items.map(nodeDate),y:items.map(nodePrice),
        text:items.map(function(node){
          return kSignalActionText(node.action) + '<br>' + esc(node.position_text || '') + '<br>' + esc(kNodeText(node));
        }),
        hovertemplate:'%{x}<br>%{text}<extra></extra>',
        marker:{symbol:symbol,size:7,color:color,line:{width:1,color:'#ffffff'}}
      };
    }
    var traces = [
      candle, volume,
      maTrace(5,'MA5','#ffc000',1.15),
      maTrace(20,'MA20','#2f75b5',1.25),
      maTrace(60,'MA60','#808080',1.35),
      markerTrace(buys,'\u4e70\u5165/\u52a0\u4ed3','#c00000','triangle-up'),
      markerTrace(sells,'\u51cf\u4ed3/\u5356\u51fa','#168a47','triangle-down')
    ];
    var selected = null;
    if(nodes.length){
      var selectedIndex = Number(S.kline.selectedSignalIndex);
      if(!Number.isFinite(selectedIndex)) selectedIndex = nodes.length - 1;
      selectedIndex = Math.max(0,Math.min(nodes.length - 1,selectedIndex));
      S.kline.selectedSignalIndex = selectedIndex;
      selected = nodes[selectedIndex];
    }
    var annotations = [];
    if(selected){
      var isBuy = /buy|add/i.test(String(selected.action || ''));
      var border = isBuy ? '#c00000' : '#168a47';
      annotations.push({
        x:nodeDate(selected),y:nodePrice(selected),xref:'x',yref:'y',
        text:'<b>' + esc(klineDate(selected.execution_date || selected.date)) + '  ' +
          esc(kSignalActionText(selected.action)) + '</b><br>' +
          esc(selected.position_text || '') + '<br>' + esc(kNodeText(selected)),
        showarrow:true,arrowhead:2,arrowwidth:1.2,arrowcolor:border,
        ax:64,ay:-74,align:'left',bgcolor:'rgba(255,255,255,.97)',
        bordercolor:border,borderwidth:1,borderpad:7,font:{size:11,color:'#111827'}
      });
      var detailNode = $('knode-detail');
      if(detailNode){
        detailNode.textContent = klineDate(selected.execution_date || selected.date) + ' | ' +
          kSignalActionText(selected.action) + ' | ' + String(selected.position_text || '') +
          ' | ' + kNodeText(selected);
      }
    }
    plot(id,daily.length ? traces : [],{
      height:510,hovermode:'x unified',
      margin:{l:54,r:22,t:48,b:62},
      xaxis:{
        type:'date',tickformat:'%Y-%m',showgrid:false,rangeslider:{visible:false},
        rangeselector:{x:0,y:1.08,font:{size:10},buttons:[
          {count:6,label:'6\u6708',step:'month',stepmode:'backward'},
          {count:1,label:'1\u5e74',step:'year',stepmode:'backward'},
          {count:3,label:'3\u5e74',step:'year',stepmode:'backward'},
          {step:'all',label:'\u5168\u90e8'}
        ]}
      },
      yaxis:{domain:[.25,1],gridcolor:'#e5e7eb',fixedrange:false},
      yaxis2:{domain:[0,.17],showgrid:false,zeroline:false,fixedrange:false},
      legend:{orientation:'h',y:-.17,x:0,font:{size:11}},
      annotations:annotations
    });
  }
  function klineEquity(id,j){
    var panelData = obj(obj(j.summary).backtest_panel);
    var equity = arr(panelData.equity);
    var x = equity.map(function(row){return klineDate(row[0]);});
    var traces = [];
    if(equity.length){
      traces.push({type:'scatter',mode:'lines',name:'\u5b66\u4e60\u540e\u7b56\u7565',x:x,y:equity.map(function(row){return Number(row[1]);}),line:{color:'#c00000',width:2.4}});
      if(Object.keys(obj(panelData.prior_model_metrics)).length){
        traces.push({type:'scatter',mode:'lines',name:'\u5347\u7ea7\u524d\u6a21\u578b',x:x,y:equity.map(function(row){return Number(row[7]);}),line:{color:'#7c3aed',width:1.9,dash:'dot'}});
      }
      if(Object.keys(obj(panelData.raw_rule_metrics)).length){
        traces.push({type:'scatter',mode:'lines',name:'\u539f\u59cbK\u7ebf\u89c4\u5219\uff08\u672a\u5b66\u4e60\uff09',x:x,y:equity.map(function(row){return Number(row[6]);}),line:{color:'#2f75b5',width:1.9}});
      }
      traces.push({type:'scatter',mode:'lines',name:'\u4e70\u5165\u6301\u6709',x:x,y:equity.map(function(row){return Number(row[5]);}),line:{color:'#808080',width:1.8}});
    }
    var validRow = equity.find(function(row){return row[4] === 'valid';});
    var testRow = equity.find(function(row){return row[4] === 'test';});
    var shapes = [];
    var annotations = [];
    [[validRow,'\u9a8c\u8bc1\u96c6'],[testRow,'\u6d4b\u8bd5\u96c6']].forEach(function(item){
      if(!item[0]) return;
      var date = klineDate(item[0][0]);
      shapes.push({type:'line',xref:'x',yref:'paper',x0:date,x1:date,y0:0,y1:1,line:{color:'#a16207',width:1.2,dash:'dash'}});
      annotations.push({xref:'x',yref:'paper',x:date,y:1,text:item[1],showarrow:false,xanchor:'left',font:{size:10,color:'#a16207'},bgcolor:'rgba(255,255,255,.86)'});
    });
    plot(id,traces,{
      height:390,hovermode:'x unified',margin:{l:54,r:22,t:28,b:58},
      legend:{orientation:'h',y:-.19,x:0,font:{size:11}},
      yaxis:{gridcolor:'#e5e7eb',title:'\u51c0\u503c'},
      xaxis:{type:'date',tickformat:'%Y-%m',showgrid:false},
      shapes:shapes,annotations:annotations
    });
  }
  function klineMetricRows(metrics,strategy){
    return Object.keys(obj(metrics)).map(function(setName){
      var metric = obj(metrics[setName]);
      return {
        strategy:strategy,
        set:{train:'\u8bad\u7ec3\u96c6',valid:'\u9a8c\u8bc1\u96c6',test:'\u6d4b\u8bd5\u96c6',full:'\u5168\u6837\u672c'}[setName] || setName,
        total_return:fmt(Number(metric.total_return || 0) * 100,2) + '%',
        annual_return:fmt(Number(metric.annual_return || 0) * 100,2) + '%',
        max_drawdown:fmt(Number(metric.max_drawdown || 0) * 100,2) + '%',
        sharpe:fmt(metric.sharpe || 0,2),
        calmar:fmt(metric.calmar || 0,2),
        avg_position:fmt(Number(metric.avg_position || 0) * 100,2) + '%',
        signal_trigger_count:Number(metric.signal_trigger_count || 0),
        buy_hold_return:fmt(Number(metric.buy_hold_return || 0) * 100,2) + '%'
      };
    });
  }
  function klineAnnualRows(equity,nodes){
    var grouped = {};
    equity.forEach(function(row){
      var year = String(row[0] || '').slice(0,4);
      if(!year) return;
      if(!grouped[year]) grouped[year] = [];
      grouped[year].push(row);
    });
    return Object.keys(grouped).sort().map(function(year){
      var rows = grouped[year];
      var first = rows[0], last = rows[rows.length - 1];
      function periodReturn(index){
        var start = Number(first[index]);
        var end = Number(last[index]);
        return start > 0 ? end / start - 1 : 0;
      }
      var peak = Number(first[1]) || 1;
      var maxDrawdown = 0;
      rows.forEach(function(row){
        var value = Number(row[1]) || 0;
        peak = Math.max(peak,value);
        if(peak > 0) maxDrawdown = Math.min(maxDrawdown,value / peak - 1);
      });
      var learned = periodReturn(1);
      var buyHold = periodReturn(5);
      var hasRaw = Number.isFinite(Number(first[6])) && Number.isFinite(Number(last[6]));
      var hasPrior = Number.isFinite(Number(first[7])) && Number.isFinite(Number(last[7]));
      var signalCount = nodes.filter(function(node){
        return String(node.execution_date || node.date || '').slice(0,4) === year;
      }).length;
      var avgPosition = rows.reduce(function(total,row){return total + Number(row[3] || 0);},0) / Math.max(rows.length,1);
      return {
        year:year,
        learned_return:fmt(learned * 100,2) + '%',
        prior_model_return:hasPrior ? fmt(periodReturn(7) * 100,2) + '%' : '--',
        raw_rule_return:hasRaw ? fmt(periodReturn(6) * 100,2) + '%' : '--',
        buy_hold_return:fmt(buyHold * 100,2) + '%',
        excess_return:fmt((learned - buyHold) * 100,2) + '%',
        max_drawdown:fmt(maxDrawdown * 100,2) + '%',
        avg_position:fmt(avgPosition * 100,2) + '%',
        signal_count:signalCount
      };
    });
  }
  async function klineBacktest(){
    await needKline();
    var j = currentKlineJob();
    if(j.job_id && !j.summary) j = await loadKlineJob(j.job_id);
    var sum = obj(j.summary);
    var panelData = obj(sum.backtest_panel);
    var metrics = obj(sum.backtest_metrics || panelData.metrics);
    var rawMetrics = obj(panelData.raw_rule_metrics);
    var priorMetrics = obj(panelData.prior_model_metrics);
    var nodes = arr(sum.signal_nodes);
    var equity = arr(panelData.equity);
    var metricsRows = klineMetricRows(metrics,'\u5b66\u4e60\u540e\u7b56\u7565').concat(
      klineMetricRows(priorMetrics,'\u5347\u7ea7\u524d\u6a21\u578b'),
      klineMetricRows(rawMetrics,'\u539f\u59cbK\u7ebf\u89c4\u5219')
    );
    var fullMetric = obj(metrics.full);
    var testMetric = obj(metrics.test);
    conclusion('\u56de\u6d4b\u4efb\u52a1 ' + esc(j.job_id || '--') +
      '\uff1a\u5168\u6837\u672c\u6536\u76ca ' + fmt(Number(fullMetric.total_return || 0) * 100,2) +
      '%\uff0c\u6d4b\u8bd5\u96c6\u6536\u76ca ' + fmt(Number(testMetric.total_return || 0) * 100,2) +
      '%\uff0c\u6700\u5927\u56de\u64a4 ' + fmt(Number(fullMetric.max_drawdown || 0) * 100,2) + '%\u3002');
    var c1 = pid('kc'), c2 = pid('ke');
    var selectedIndex = Number(S.kline.selectedSignalIndex);
    if(!Number.isFinite(selectedIndex)) selectedIndex = Math.max(nodes.length - 1,0);
    selectedIndex = Math.max(0,Math.min(Math.max(nodes.length - 1,0),selectedIndex));
    S.kline.selectedSignalIndex = selectedIndex;
    var options = nodes.length ? nodes.map(function(node,index){
      return '<option value="' + index + '"' + (index === selectedIndex ? ' selected' : '') + '>' +
        esc(klineDate(node.execution_date || node.date)) + ' | ' +
        esc(kSignalActionText(node.action)) + ' | ' + esc(node.position_text || '') + '</option>';
    }).join('') : '<option value="0">\u6682\u65e0\u8c03\u4ed3\u4fe1\u53f7</option>';
    var nodeControl = '<section class="signal-node-toolbar"><label>\u4fe1\u53f7\u8282\u70b9<select id="knode">' +
      options + '</select></label><p id="knode-detail"></p></section>';
    var body = nodeControl;
    body += '<div class="panel-grid full">' +
      panel(c1,'\u4fe1\u53f7K\u7ebf\u56fe','\u5168\u5386\u53f2\u590d\u6743K\u7ebf\u3001\u6210\u4ea4\u91cf\u3001MA5/20/60\u4e0e\u53ef\u9009\u4fe1\u53f7\u6279\u6ce8',true) +
      panel(c2,'\u56de\u6d4b\u51c0\u503c','\u5b66\u4e60\u540e\u7b56\u7565\u3001\u5347\u7ea7\u524d\u6a21\u578b\u3001\u539f\u59cbK\u7ebf\u89c4\u5219\u4e0e\u4e70\u5165\u6301\u6709',true) +
      '</div>';
    body += tableHTML('\u56de\u6d4b\u6307\u6807',metricsRows,[
      'strategy','set','total_return','annual_return','max_drawdown','sharpe','calmar',
      'avg_position','signal_trigger_count','buy_hold_return'
    ]);
    body += tableHTML('\u5e74\u5ea6\u6536\u76ca\u5f52\u56e0',klineAnnualRows(equity,nodes),[
      'year','learned_return','prior_model_return','raw_rule_return','buy_hold_return','excess_return',
      'max_drawdown','avg_position','signal_count'
    ]);
    root(body);
    klineCandle(c1,j);
    klineEquity(c2,j);
    if($('knode')){
      $('knode').disabled = !nodes.length;
      $('knode').onchange = function(){
        S.kline.selectedSignalIndex = Number($('knode').value || 0);
        klineCandle(c1,j);
      };
    }
  }

  /* Asset allocation r20: auditable cycle factors, profile-constrained solvers and sealed-test review. */
  S.allocation = S.allocation || {snapshot:null,cycleModel:'pring',lookback:'60',factorMode:'components',strategy:'recommended',riskProfile:'equity_preferred',backtest:['recommended','all_weather','risk_parity'],backtestWindow:'full',navMode:'net',reportHtml:''};
  Object.assign(HEAD,{
    'allocation:home':['资产配置主页','多周期研判、风险预算与当前权重'],
    'allocation:cycle':['周期跟踪','因子轨迹、状态概率与历史复盘'],
    'allocation:strategy':['配置策略','全天候、风险平价与多模型求解'],
    'allocation:backtest':['回测检验','训练验证测试、成本后净值与过拟合审计']
  });
  Object.assign(COL,{
    strategy:'策略',asset:'资产',capital_weight:'资本权重',risk_contribution:'风险贡献',constraint:'可行域',
    phase:'阶段',phase_name:'阶段名称',bits:'三比特',leading:'先行因子',coincident:'同步因子',lagging:'滞后因子',
    confidence:'置信度',state:'周期状态',month:'月份',annual_return:'年化收益',annual_volatility:'年化波动率',
    sharpe:'Sharpe',calmar:'Calmar',max_drawdown:'最大回撤',total_return:'累计收益',positive_month_rate:'月度胜率',
    average_annual_turnover:'年均换手',cost_drag:'成本拖累',months:'样本月数',average_monthly_return:'月均收益',positive_rate:'正收益占比',
    horizon:'观察周期',inputs:'输入因子',transform:'变换方法',allocation_role:'配置作用',equity:'权益',bond:'债券',commodity:'商品',cash:'现金',
    factor_1:'因子一',factor_2:'因子二',factor_3:'因子三',role:'因子角色',name:'名称',train_ic:'训练期IC',block_stability:'分块稳定度',
    band_power:'周期频带能量',score:'综合分',observations:'有效样本',max_selected_correlation:'最大入选相关性',sample_set:'样本区间',
    code:'ETF代码',provider:'数据来源',historical_predecessor:'历史衔接ETF',parameter:'参数',value:'取值',status:'状态',
    train_sharpe:'训练Sharpe',validation_sharpe:'验证Sharpe',test_sharpe_report_only:'测试Sharpe（仅报告）',validation_drawdown:'验证回撤',turnover:'换手率',
    transaction_cost_bps:'单边成本(bp)'
  });
  const ALLOC_ASSETS=['equity','bond','commodity','cash'];
  const ALLOC_ASSET_CN={equity:'权益',bond:'债券',commodity:'商品',cash:'现金'};
  const ALLOC_STRATEGY_CN={equal_weight:'等权',risk_parity:'风险平价',all_weather:'全天候',hrp:'层次风险平价',macro_risk_budget:'宏观风险预算',robust_bl:'稳健Black-Litterman',pring_stage:'普林格阶段配置',hmm_risk_parity:'HMM风险平价',cycle_risk_parity:'周期风险预算',recommended:'推荐组合'};
  const ALLOC_COLORS={equity:'#c00000',bond:'#2f75b5',commodity:'#ed7d31',cash:'#808080'};
  const PHASE_COLORS={1:'rgba(47,117,181,.10)',2:'rgba(192,0,0,.08)',3:'rgba(237,125,49,.10)',4:'rgba(255,192,0,.12)',5:'rgba(112,48,160,.09)',6:'rgba(0,176,80,.09)'};
  const SAMPLE_COLORS={train:'rgba(128,128,128,.08)',validation:'rgba(237,125,49,.08)',test:'rgba(0,176,80,.07)'};
  const SAMPLE_CN={train:'训练集',validation:'验证集',test:'测试集',full:'全样本'};
  function allocMonth(value){ const s=String(value||'').replace(/\D/g,''); return s.length>=6?s.slice(0,4)+'-'+s.slice(4,6):String(value||'--'); }
  function allocPct(value,digits){ let v=Number(value);if(Math.abs(v)<.00005)v=0;return Number.isFinite(v)?fmt(v*100,digits==null?2:digits)+'%':'--'; }
  function allocNumber(value){ const v=Number(value); return Number.isFinite(v)?fmt(v,2):'--'; }
  function allocRangeSlider(){ return {visible:true,thickness:.06,bgcolor:'rgba(255,255,255,0)',bordercolor:'#d9dee5',borderwidth:1}; }
  function allocStrategyOptions(selected){ return Object.keys(ALLOC_STRATEGY_CN).map(function(key){return '<option value="'+key+'"'+(key===selected?' selected':'')+'>'+ALLOC_STRATEGY_CN[key]+'</option>';}).join(''); }
  async function needAllocation(){ if(S.allocation.snapshot)return S.allocation.snapshot;S.allocation.snapshot=await api('/api/allocation/snapshot');return S.allocation.snapshot; }
  function allocCurrent(data){ return obj(obj(data.allocations).current_cycle); }
  function allocPortfolio(data,strategy,profile){ const allocations=obj(data.allocations);if(strategy==='recommended'&&obj(allocations.profiles)[profile])return obj(obj(allocations.profiles)[profile]);return obj(allocations[strategy]); }
  function allocWeights(data,strategy,profile){ return obj(allocPortfolio(data,strategy,profile).weights); }
  function allocRisk(data,strategy,profile){ return obj(allocPortfolio(data,strategy,profile).risk_contribution); }
  function allocStateCards(items){ return '<div class="allocation-state-grid">'+items.map(function(item){return '<article class="allocation-state-card '+esc(item.tone||'')+'"><small>'+esc(item.label)+'</small><strong>'+esc(item.value)+'</strong><p>'+esc(item.note||'')+'</p></article>';}).join('')+'</div>'; }
  function allocPhaseShapes(rows){ const shapes=[];if(!rows.length)return shapes;let start=0;for(let i=1;i<=rows.length;i++)if(i===rows.length||rows[i].pring_phase!==rows[start].pring_phase){shapes.push({type:'rect',xref:'x',yref:'paper',x0:allocMonth(rows[start].month)+'-01',x1:allocMonth(rows[Math.min(i,rows.length-1)].month)+'-28',y0:0,y1:1,fillcolor:PHASE_COLORS[rows[start].pring_phase]||'rgba(0,0,0,.03)',line:{width:0},layer:'below'});start=i;}return shapes; }
  function allocProfileSpec(data,profile){ return obj(obj(data.profiles)[profile]); }
  function allocConstraint(data,profile,asset,weight){ const spec=allocProfileSpec(data,profile),index=ALLOC_ASSETS.indexOf(asset),floor=Number(arr(spec.floors)[index]),cap=Number(arr(spec.caps)[index]);if(Math.abs(weight-floor)<.001)return '下限 '+allocPct(floor);if(Math.abs(weight-cap)<.001)return '上限 '+allocPct(cap);return allocPct(floor)+' – '+allocPct(cap); }
  function allocWeightRows(data){ return ['recommended','all_weather','risk_parity','hrp','macro_risk_budget','robust_bl','cycle_risk_parity','hmm_risk_parity'].map(function(strategy){const w=allocWeights(data,strategy,'equity_preferred');return {strategy:ALLOC_STRATEGY_CN[strategy],equity:allocPct(w.equity),bond:allocPct(w.bond),commodity:allocPct(w.commodity),cash:allocPct(w.cash)};}); }
  function allocWeightDonut(id,weights,title){ plot(id,[{type:'pie',hole:.62,labels:ALLOC_ASSETS.map(function(a){return ALLOC_ASSET_CN[a];}),values:ALLOC_ASSETS.map(function(a){return Number(weights[a]||0);}),marker:{colors:ALLOC_ASSETS.map(function(a){return ALLOC_COLORS[a];})},texttemplate:'%{label}<br>%{percent:.2%}',textinfo:'label+percent',hovertemplate:'%{label} %{percent:.2%}<extra></extra>'}],{height:330,showlegend:false,annotations:[{text:title||'资本权重',showarrow:false,font:{size:14,color:'#344054'}}],margin:{l:20,r:20,t:20,b:20}}); }
  function allocWeightCompare(id,data){ const keys=['recommended','all_weather','risk_parity','hrp','macro_risk_budget'];plot(id,ALLOC_ASSETS.map(function(asset){return {type:'bar',name:ALLOC_ASSET_CN[asset],x:keys.map(function(k){return ALLOC_STRATEGY_CN[k];}),y:keys.map(function(k){return Number(allocWeights(data,k,'equity_preferred')[asset]||0)*100;}),marker:{color:ALLOC_COLORS[asset]},hovertemplate:'%{x}<br>'+ALLOC_ASSET_CN[asset]+' %{y:.2f}%<extra></extra>'};}),{height:330,barmode:'stack',yaxis:{title:'资本权重（%）',range:[0,100]},xaxis:{showgrid:false},legend:{orientation:'h',y:-.25}}); }
  function allocETFRows(data){ return ALLOC_ASSETS.map(function(asset){const p=obj(obj(data.asset_proxies)[asset]);return {asset:ALLOC_ASSET_CN[asset],code:p.ts_code,name:p.name,provider:p.provider,historical_predecessor:p.historical_predecessor||'--'};}); }
  function allocSelectedFactors(data,roles){ const out=[],seen=new Set();roles.forEach(function(role){arr(obj(obj(data.factor_selection).roles)[role]).forEach(function(row){const key=role+':'+row.id;if(seen.has(key))return;seen.add(key);out.push({role:role,name:row.name,transform:row.transform,train_ic:allocNumber(row.train_ic),block_stability:allocPct(row.block_stability),band_power:allocPct(row.band_power),score:allocNumber(row.score),observations:Math.round(Number(row.observations)||0),max_selected_correlation:allocNumber(row.max_selected_correlation)});});});return out; }
  async function allocationHome(){
    const data=await needAllocation(),cycle=allocCurrent(data),w=allocWeights(data,'recommended','equity_preferred');
    header('资产配置主页','多周期研判、风险预算与当前权重','资产配置');setText('as-of',allocMonth(obj(data.data_as_of).market));setText('generated-at',String(data.generated_at||'--').replace('T',' ').replace('Z',''));
    conclusion('宏观完整月 '+allocMonth(obj(data.data_as_of).macro_complete)+'：普林格'+esc(cycle.pring_phase_name||'--')+'，基钦'+esc(cycle.kitchin_state||'--')+'，朱格拉'+esc(cycle.juglar_state||'--')+'，康波'+esc(cycle.kondratieff_state||'--')+'，美林'+esc(cycle.merrill_state||'--')+'；权益偏好组合为权益 '+allocPct(w.equity)+'、债券 '+allocPct(w.bond)+'、商品 '+allocPct(w.commodity)+'、现金 '+allocPct(w.cash)+'。');
    const states=allocStateCards([
      {label:'普林格',value:'阶段'+cycle.pring_phase+' · '+cycle.pring_phase_name,note:'置信度 '+allocPct(cycle.confidence),tone:'ok'},
      {label:'基钦周期',value:cycle.kitchin_state,note:'最高状态概率 '+allocPct(Math.max.apply(null,Object.values(obj(cycle.kitchin_probability)).map(Number))),tone:'ok'},
      {label:'朱格拉周期',value:cycle.juglar_state,note:'最高状态概率 '+allocPct(Math.max.apply(null,Object.values(obj(cycle.juglar_probability)).map(Number))),tone:'ok'},
      {label:'康波 / 美林',value:cycle.kondratieff_state+' / '+cycle.merrill_state,note:'康波置信度 '+allocPct(cycle.kondratieff_confidence),tone:'muted'}
    ]);
    const c1=pid('ah'),c2=pid('ah'),c3=pid('ah');const registry=arr(data.factor_registry).map(function(row){return {name:row.name,horizon:row.horizon,inputs:arr(row.inputs).join('、'),transform:row.transform,allocation_role:row.allocation_role};});
    const selection=obj(data.factor_selection);const factors=allocSelectedFactors(data,['leading','coincident','lagging']);
    root(states+'<div class="panel-grid">'+panel(c1,'推荐组合','权益偏好可行域')+panel(c2,'核心策略权重对照','统一四资产ETF口径')+'</div>'+panel(c3,'普林格三类复合因子','概率阶段底色与因子轨迹',true)+tableHTML('当前可交易ETF代理',allocETFRows(data),['asset','code','name','provider','historical_predecessor'])+tableHTML('训练期入选因子 · '+selection.candidate_count+'个候选 / '+selection.selected_unique_count+'个唯一入选',factors,['role','name','transform','train_ic','block_stability','band_power','score','observations','max_selected_correlation'])+tableHTML('策略权重矩阵',allocWeightRows(data),['strategy','equity','bond','commodity','cash'])+tableHTML('模型与因子注册表',registry,['name','horizon','inputs','transform','allocation_role']));
    allocWeightDonut(c1,w,'权益偏好');allocWeightCompare(c2,data);allocPringFactorChart(c3,arr(data.cycle_history));
  }
  function allocPringFactorChart(id,rows){ const x=rows.map(function(r){return allocMonth(r.month)+'-01';}),traces=[['leading','先行','#2f75b5'],['coincident','同步','#c00000'],['lagging','滞后','#ed7d31']].map(function(item){return {type:'scatter',mode:'lines',name:item[1],x:x,y:rows.map(function(r){return Number(r[item[0]])*100;}),line:{color:item[2],width:2.1},hovertemplate:'%{x|%Y-%m}<br>'+item[1]+' %{y:.2f}<extra></extra>'};});plot(id,traces,{height:390,hovermode:'x unified',shapes:allocPhaseShapes(rows),yaxis:{title:'方向概率（%）',range:[0,100]},xaxis:{type:'date',rangeselector:{buttons:[{count:3,label:'3年',step:'year',stepmode:'backward'},{count:5,label:'5年',step:'year',stepmode:'backward'},{step:'all',label:'全部'}]},rangeslider:allocRangeSlider()},legend:{orientation:'h',y:-.28}}); }
  function allocCycleSpec(model){ return {
    pring:{title:'普林格六阶段',state:'pring_phase_name',prob:'pring_probability',roles:['leading','coincident','lagging'],composite:[['leading','先行方向概率'],['coincident','同步方向概率'],['lagging','滞后方向概率']],percent:true},
    kitchin:{title:'基钦库存周期',state:'kitchin_state',prob:'kitchin_probability',roles:['demand','inventory'],composite:[['kitchin_demand_score','需求得分'],['kitchin_inventory_proxy','价格/库存代理']]},
    juglar:{title:'朱格拉资本开支周期',state:'juglar_state',prob:'juglar_probability',roles:['juglar','credit'],composite:[['juglar_credit_score','信用得分'],['juglar_slope','中周期斜率']]},
    kondratieff:{title:'康波结构情景',state:'kondratieff_state',prob:'kondratieff_probability',roles:['growth','inflation','credit'],composite:[['growth_score','慢增长代理'],['inflation_score','慢通胀代理'],['credit_score','慢信用代理']]},
    merrill:{title:'美林投资时钟',state:'merrill_state',prob:'merrill_probability',roles:['growth','inflation'],composite:[['growth_score','增长得分'],['inflation_score','通胀得分']]}
  }[model]; }
  function allocFactorChart(id,data,rows,spec,mode){ const x=rows.map(function(r){return allocMonth(r.month)+'-01';}),traces=[];let ytitle='标准化得分';
    if(mode==='probability'){const labels=Array.from(new Set(rows.flatMap(function(r){return Object.keys(obj(r[spec.prob]));})));labels.forEach(function(label,index){traces.push({type:'scatter',mode:'lines',name:String(label),x:x,y:rows.map(function(r){return Number(obj(r[spec.prob])[label]||0)*100;}),line:{width:2,color:CHART_PALETTE[index%CHART_PALETTE.length]},hovertemplate:'%{x|%Y-%m}<br>'+esc(label)+' %{y:.2f}%<extra></extra>'});});ytitle='状态概率（%）';}
    else if(mode==='selected'){const series=obj(data.factor_series),used=new Set();spec.roles.forEach(function(role){arr(obj(obj(data.factor_selection).roles)[role]).forEach(function(factor){if(used.has(factor.id))return;used.add(factor.id);const by={};arr(series[factor.id]).forEach(function(r){by[String(r.month)]=r.value;});traces.push({type:'scatter',mode:'lines',name:factor.name,x:x,y:rows.map(function(r){const v=by[String(r.month)];return v==null?null:Number(v);}),line:{width:1.8,color:CHART_PALETTE[(traces.length)%CHART_PALETTE.length]},hovertemplate:'%{x|%Y-%m}<br>'+esc(factor.name)+' %{y:.2f}<extra></extra>'});});});}
    else{spec.composite.forEach(function(item,index){traces.push({type:'scatter',mode:'lines',name:item[1],x:x,y:rows.map(function(r){const v=Number(r[item[0]]);return spec.percent?v*100:v;}),line:{width:2.2,color:CHART_PALETTE[index%CHART_PALETTE.length]},hovertemplate:'%{x|%Y-%m}<br>'+item[1]+' %{y:.2f}<extra></extra>'});});if(spec.percent)ytitle='方向概率（%）';}
    plot(id,traces,{height:400,hovermode:'x unified',shapes:S.allocation.cycleModel==='pring'?allocPhaseShapes(rows):[],yaxis:{title:ytitle},xaxis:{type:'date',rangeslider:allocRangeSlider()},legend:{orientation:'h',y:-.28}});
  }
  function allocProbabilityChart(id,rows,spec){ const labels=Array.from(new Set(rows.flatMap(function(r){return Object.keys(obj(r[spec.prob]));}))),x=rows.map(function(r){return allocMonth(r.month)+'-01';});plot(id,labels.map(function(label,index){return {type:'scatter',mode:'lines',name:String(label),x:x,y:rows.map(function(r){return Number(obj(r[spec.prob])[label]||0)*100;}),line:{width:2,color:CHART_PALETTE[index%CHART_PALETTE.length]},hovertemplate:'%{x|%Y-%m}<br>'+esc(label)+' %{y:.2f}%<extra></extra>'};}),{height:400,hovermode:'x unified',yaxis:{title:'状态概率（%）',range:[0,100]},xaxis:{type:'date'},legend:{orientation:'h',y:-.28}}); }
  function allocConfidence(row,model,spec){ if(model==='pring')return Number(row.confidence);if(model==='kondratieff')return Number(row.kondratieff_confidence);const values=Object.values(obj(row[spec.prob])).map(Number);return values.length?Math.max.apply(null,values):0; }
  async function allocationCycle(){
    const data=await needAllocation(),model=S.allocation.cycleModel,spec=allocCycleSpec(model),lookback=S.allocation.lookback;let rows=arr(data.cycle_history);if(lookback!=='all')rows=rows.slice(-Number(lookback));const current=rows[rows.length-1]||{};
    header('周期跟踪','因子轨迹、状态概率与历史复盘','资产配置');setText('as-of',allocMonth(obj(data.data_as_of).macro_complete));setText('generated-at',String(data.generated_at||'--').replace('T',' ').replace('Z',''));
    conclusion('当前 '+spec.title+'：'+esc(model==='pring'?'阶段'+current.pring_phase+' · '+current.pring_phase_name:current[spec.state]||'--')+'，置信度 '+allocPct(allocConfidence(current,model,spec))+'。');
    const c1=pid('ac'),c2=pid('ac');const controls='<section class="control-card"><div class="control-grid"><label>周期模型<select id="alloc-cycle-model"><option value="pring">普林格六阶段</option><option value="kitchin">基钦周期</option><option value="juglar">朱格拉周期</option><option value="kondratieff">康波情景</option><option value="merrill">美林时钟</option></select></label><label>观察窗口<select id="alloc-cycle-lookback"><option value="60">近5年</option><option value="120">近10年</option><option value="all">全部</option></select></label><label>主图模式<select id="alloc-factor-mode"><option value="components">模型复合因子</option><option value="selected">训练期入选原子因子</option><option value="probability">状态概率</option></select></label><div class="control-readout">当前状态<strong>'+esc(model==='pring'?'阶段'+current.pring_phase+' · '+current.pring_phase_name:current[spec.state]||'--')+'</strong></div></div></section>';
    const history=rows.slice().reverse().map(function(r){const values=spec.composite.map(function(item){return allocNumber(r[item[0]]);});return {month:allocMonth(r.month),state:model==='pring'?'阶段'+r.pring_phase+' '+r.pring_phase_name:r[spec.state],confidence:allocPct(allocConfidence(r,model,spec)),factor_1:values[0]||'--',factor_2:values[1]||'--',factor_3:values[2]||'--'};});
    const selected=allocSelectedFactors(data,spec.roles),historyColumns=spec.composite.length>2?['month','state','confidence','factor_1','factor_2','factor_3']:['month','state','confidence','factor_1','factor_2'];
    root(controls+'<div class="panel-grid">'+panel(c1,spec.title+'因子轨迹',S.allocation.factorMode==='selected'?'训练期筛选后原子因子':'复合因子与概率',true)+panel(c2,'状态概率轨迹','每月完整概率分布')+'</div>'+tableHTML(spec.title+'入选因子',selected,['role','name','transform','train_ic','block_stability','band_power','score','observations','max_selected_correlation'])+tableHTML(spec.title+'历史状态',history,historyColumns));
    $('alloc-cycle-model').value=model;$('alloc-cycle-lookback').value=lookback;$('alloc-factor-mode').value=S.allocation.factorMode;
    allocFactorChart(c1,data,rows,spec,S.allocation.factorMode);allocProbabilityChart(c2,rows,spec);
    $('alloc-cycle-model').onchange=function(){S.allocation.cycleModel=this.value;allocationCycle();};$('alloc-cycle-lookback').onchange=function(){S.allocation.lookback=this.value;allocationCycle();};$('alloc-factor-mode').onchange=function(){S.allocation.factorMode=this.value;allocationCycle();};
  }
  function allocDynamicWeights(id,data,strategy){ const rows=arr(obj(obj(obj(data.backtest).strategies)[strategy]).weights),x=rows.map(function(r){return allocMonth(r.month)+'-01';});plot(id,ALLOC_ASSETS.map(function(a){return {type:'scatter',mode:'lines',stackgroup:'one',groupnorm:'percent',name:ALLOC_ASSET_CN[a],x:x,y:rows.map(function(r){return Number(r[a]);}),line:{width:.8,color:ALLOC_COLORS[a]},hovertemplate:'%{x|%Y-%m}<br>'+ALLOC_ASSET_CN[a]+' %{y:.2%}<extra></extra>'};}),{height:390,hovermode:'x unified',yaxis:{title:'资本权重（%）',ticksuffix:'%'},xaxis:{type:'date',rangeslider:allocRangeSlider()},legend:{orientation:'h',y:-.28}}); }
  function allocAuditRows(data){ const audit=obj(data.optimization),spec=obj(audit.selected_spec),gate=obj(audit.promotion_gate);return [
    {parameter:'候选规格',value:spec.id||'--'},{parameter:'协方差估计',value:spec.covariance_method||'--'},{parameter:'滚动窗口',value:Math.round(Number(spec.lookback)||0)+'个月'},
    {parameter:'候选数量',value:Math.round(Number(audit.trial_count)||0)},{parameter:'CSCV-PBO',value:allocPct(audit.pbo_cscv)},{parameter:'Deflated Sharpe概率',value:allocPct(audit.deflated_sharpe_probability)},
    {parameter:'上线门槛',value:gate.status==='passed'?'通过':'条件性输出'}
  ]; }
  async function allocationStrategy(){
    const data=await needAllocation(),strategy=S.allocation.strategy,requestedProfile=S.allocation.riskProfile,profile=strategy==='recommended'?requestedProfile:'balanced',portfolio=allocPortfolio(data,strategy,profile),w=obj(portfolio.weights),rc=obj(portfolio.risk_contribution);
    header('配置策略','全天候、风险平价与多模型求解','资产配置');setText('as-of',allocMonth(obj(data.data_as_of).market));setText('generated-at',String(data.generated_at||'--').replace('T',' ').replace('Z',''));
    conclusion((ALLOC_STRATEGY_CN[strategy]||strategy)+' · '+(obj(obj(data.profiles)[profile]).label||profile)+'：权益 '+allocPct(w.equity)+'、债券 '+allocPct(w.bond)+'、商品 '+allocPct(w.commodity)+'、现金 '+allocPct(w.cash)+'。');
    const c1=pid('as'),c2=pid('as'),spec=obj(data.optimization).selected_spec||{};const controls='<section class="control-card"><div class="control-grid"><label>配置策略<select id="alloc-strategy">'+allocStrategyOptions(strategy)+'</select></label><label>投资者画像<select id="alloc-risk"'+(strategy==='recommended'?'':' disabled')+'><option value="conservative">稳健</option><option value="balanced">平衡</option><option value="equity_preferred">权益偏好</option></select></label><div class="control-readout">选中规格<strong>'+esc(spec.id||'--')+'</strong></div><div class="control-readout">协方差 / 窗口<strong>'+esc(spec.covariance_method||'--')+' / '+Math.round(Number(spec.lookback)||0)+'月</strong></div></div></section>';
    const weightRows=ALLOC_ASSETS.map(function(a){return {asset:ALLOC_ASSET_CN[a],capital_weight:allocPct(w[a]),risk_contribution:allocPct(rc[a]),constraint:allocConstraint(data,profile,a,Number(w[a]))};});
    const strategyRows=Object.keys(ALLOC_STRATEGY_CN).map(function(k){const m=obj(obj(obj(obj(data.backtest).strategies)[k]).metrics),weights=allocWeights(data,k,k==='recommended'?'equity_preferred':'balanced');return {strategy:ALLOC_STRATEGY_CN[k],equity:allocPct(weights.equity),bond:allocPct(weights.bond),commodity:allocPct(weights.commodity),cash:allocPct(weights.cash),annual_return:allocPct(m.annual_return),annual_volatility:allocPct(m.annual_volatility),sharpe:allocNumber(m.sharpe),max_drawdown:allocPct(m.max_drawdown),average_annual_turnover:allocPct(m.average_annual_turnover)};});
    let blend=Object.entries(obj(spec.blend)).map(function(item){return {parameter:ALLOC_STRATEGY_CN[item[0]]||item[0],value:allocPct(item[1])};});if(!blend.length&&spec.anchor!=null)blend=[{parameter:'战略先验锚',value:allocPct(spec.anchor)},{parameter:'战术后验基础权重',value:allocPct(1-Number(spec.anchor))},{parameter:'稳定风险袖套上限',value:allocPct(Number(spec.stability_base||0)+Number(spec.stability_max||0))},{parameter:'权益防守转移上限',value:allocPct(spec.equity_guard_max)},{parameter:'宏观周期倾斜强度',value:allocPct(spec.macro_strength)}];
    root(controls+'<div class="panel-grid">'+panel(c1,'求解后资本权重','服务端约束与多模型融合')+panel(c2,'历史动态权重','月末信号配置下一月')+'</div>'+tableHTML('资本权重与风险贡献',weightRows,['asset','capital_weight','risk_contribution','constraint'])+tableHTML('验证期选模审计',allocAuditRows(data),['parameter','value'])+tableHTML('入选规格的模型融合系数',blend,['parameter','value'])+tableHTML('策略与回测对照',strategyRows,['strategy','equity','bond','commodity','cash','annual_return','annual_volatility','sharpe','max_drawdown','average_annual_turnover']));
    $('alloc-risk').value=profile;allocWeightDonut(c1,w,ALLOC_STRATEGY_CN[strategy]);allocDynamicWeights(c2,data,strategy);
    $('alloc-strategy').onchange=function(){S.allocation.strategy=this.value;allocationStrategy();};$('alloc-risk').onchange=function(){S.allocation.riskProfile=this.value;allocationStrategy();};
  }
  function allocWindowRows(rows,windowName){ if(windowName==='3y')return rows.slice(-36);if(windowName==='5y')return rows.slice(-60);return rows; }
  function allocSampleDecorations(rows){ const shapes=[],annotations=[];if(!rows.length)return {shapes:shapes,annotations:annotations};let start=0;for(let i=1;i<=rows.length;i++)if(i===rows.length||rows[i].sample_set!==rows[start].sample_set){const sample=rows[start].sample_set||'test';shapes.push({type:'rect',xref:'x',yref:'paper',x0:allocMonth(rows[start].month)+'-01',x1:allocMonth(rows[Math.min(i,rows.length-1)].month)+'-28',y0:0,y1:1,fillcolor:SAMPLE_COLORS[sample]||'rgba(0,0,0,.03)',line:{width:0},layer:'below'});annotations.push({xref:'x',yref:'paper',x:allocMonth(rows[start].month)+'-08',y:1.03,text:SAMPLE_CN[sample]||sample,showarrow:false,xanchor:'left',font:{size:10,color:'#667085'}});start=i;}return {shapes:shapes,annotations:annotations}; }
  function allocDrawBacktest(navId,ddId,annualId,data,strategies,windowName,mode){ const traces=[],drawdowns=[],annualYears=new Set(),annualBy={};let decorationRows=[];
    strategies.forEach(function(key){const rows=allocWindowRows(arr(obj(obj(obj(data.backtest).strategies)[key]).nav),windowName),field=mode==='gross'?'gross_nav':'nav',base=rows.length?Number(rows[0][field]):1;let peak=1,years={};if(!decorationRows.length)decorationRows=rows;const nav=rows.map(function(r){return Number(r[field])/base;}),x=rows.map(function(r){return allocMonth(r.month)+'-01';});traces.push({type:'scatter',mode:'lines',name:ALLOC_STRATEGY_CN[key],x:x,y:nav,line:{width:key==='recommended'?2.8:1.8}});drawdowns.push({type:'scatter',mode:'lines',name:ALLOC_STRATEGY_CN[key],x:x,y:nav.map(function(v){peak=Math.max(peak,v);return (v/peak-1)*100;}),line:{width:1.8}});rows.forEach(function(r,index){const year=String(r.month).slice(0,4),value=Number(r[field]),prior=index?Number(rows[index-1][field]):base,period=prior?value/prior-1:0;years[year]=(years[year]||1)*(1+period);annualYears.add(year);});annualBy[key]=years;});
    const deco=allocSampleDecorations(decorationRows);plot(navId,traces,{height:400,hovermode:'x unified',yaxis:{title:mode==='gross'?'成本前净值':'成本后净值'},xaxis:{type:'date',rangeslider:allocRangeSlider()},shapes:deco.shapes,annotations:deco.annotations,legend:{orientation:'h',y:-.28}});plot(ddId,drawdowns,{height:300,hovermode:'x unified',yaxis:{title:'回撤（%）',ticksuffix:'%'},xaxis:{type:'date'},shapes:deco.shapes,annotations:deco.annotations,legend:{orientation:'h',y:-.28}});const years=Array.from(annualYears).sort();plot(annualId,[{type:'heatmap',x:years,y:strategies.map(function(k){return ALLOC_STRATEGY_CN[k];}),z:strategies.map(function(k){return years.map(function(y){return ((annualBy[k][y]||1)-1)*100;});}),colorscale:[[0,'#00b050'],[.5,'#ffffff'],[1,'#c00000']],zmid:0,colorbar:{title:'%'},texttemplate:'%{z:.2f}',hovertemplate:'%{y}<br>%{x} %{z:.2f}%<extra></extra>'}],{height:300,xaxis:{showgrid:false},yaxis:{showgrid:false},margin:{l:110,r:45,t:20,b:45}}); }
  function allocMetricRows(data,chosen){ const rows=[];chosen.forEach(function(k){const strategy=obj(obj(obj(data.backtest).strategies)[k]),splits=obj(strategy.metrics_by_split),all=[['full',obj(strategy.metrics)],['train',obj(splits.train)],['validation',obj(splits.validation)],['test',obj(splits.test)]];all.forEach(function(item){const m=obj(item[1]);rows.push({strategy:ALLOC_STRATEGY_CN[k],sample_set:SAMPLE_CN[item[0]]||item[0],months:item[0]==='full'?Math.round(arr(strategy.nav).length):Math.round(Number(m.months)||0),annual_return:allocPct(m.annual_return),annual_volatility:allocPct(m.annual_volatility),sharpe:allocNumber(m.sharpe),calmar:allocNumber(m.calmar),max_drawdown:allocPct(m.max_drawdown),positive_month_rate:allocPct(m.positive_month_rate),total_return:allocPct(m.total_return)});});});return rows; }
  async function allocationGenerateReport(){ const button=$('alloc-gpt-report'),target=$('allocation-report');if(!button||!target)return;button.disabled=true;button.textContent='GPT报告生成中…';target.innerHTML='<p>正在汇总五类周期、入选因子、ETF权重、训练/验证/测试与最新事件流…</p>';try{const result=await api('/api/allocation/report',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});S.allocation.reportHtml=result.html||'<p>未返回报告正文。</p>';target.innerHTML=S.allocation.reportHtml;}catch(error){target.innerHTML='<p class="ai-red">报告生成失败：'+esc(error.message)+'</p>';}finally{button.disabled=false;button.textContent='GPT生成资产配置报告';} }
  async function allocationBacktest(){
    const data=await needAllocation(),chosen=S.allocation.backtest.length?S.allocation.backtest:['recommended'];header('回测检验','训练验证测试、成本后净值与过拟合审计','资产配置');setText('as-of',allocMonth(obj(data.data_as_of).market));setText('generated-at',String(data.generated_at||'--').replace('T',' ').replace('Z',''));
    const config=obj(obj(data.backtest).config),audit=obj(data.optimization),gate=obj(audit.promotion_gate);conclusion('月末信号配置下一月，单边成本 '+fmt(config.transaction_cost_bps,0)+'bp，月度换手上限 '+allocPct(config.max_turnover)+'；候选仅用训练集入围、验证集定型、测试集只报告。上线门槛：'+(gate.status==='passed'?'通过':'条件性输出')+'。');
    const c1=pid('ab'),c2=pid('ab'),c3=pid('ab');const controls='<section class="control-card"><div class="control-grid"><label style="grid-column:span 2;">对照策略（可多选）<select id="alloc-backtest-strategies" multiple size="8">'+Object.keys(ALLOC_STRATEGY_CN).map(function(k){return '<option value="'+k+'"'+(chosen.includes(k)?' selected':'')+'>'+ALLOC_STRATEGY_CN[k]+'</option>';}).join('')+'</select></label><label>观察窗口<select id="alloc-backtest-window"><option value="full">全部样本</option><option value="5y">近5年</option><option value="3y">近3年</option></select></label><label>净值口径<select id="alloc-nav-mode"><option value="net">成本后</option><option value="gross">成本前</option></select></label><button id="alloc-gpt-report" class="action-button preserve-acronym" type="button">GPT生成资产配置报告</button></div></section>';
    const leaderboard=arr(audit.leaderboard).map(function(row){return {strategy:row.id,status:row.id===obj(audit.selected_spec).id?'最终入选':row.validation_eligible?'验证入围':(row.train_eligible||row.shortlisted_by_train)?'训练入围':'未入围',train_sharpe:allocNumber(row.train_sharpe),validation_sharpe:allocNumber(row.validation_sharpe),test_sharpe_report_only:allocNumber(row.test_sharpe_report_only),validation_drawdown:allocPct(row.validation_drawdown),turnover:allocPct(row.turnover)};});
    const costs=arr(obj(obj(data.backtest).robustness).cost_sensitivity_test).map(function(row){return {transaction_cost_bps:fmt(row.transaction_cost_bps,0),annual_return:allocPct(row.annual_return),annual_volatility:allocPct(row.annual_volatility),sharpe:allocNumber(row.sharpe),max_drawdown:allocPct(row.max_drawdown)};});
    root(controls+'<div id="allocation-report" class="ai-panel is-compact preserve-acronym">'+(S.allocation.reportHtml||'')+'</div>'+panel(c1,'策略净值曲线','训练集 / 验证集 / 测试集',true)+'<div class="panel-grid">'+panel(c2,'动态回撤','与净值相同的样本分段')+panel(c3,'年度收益热力图','完整年度与部分年度')+'</div>'+tableHTML('分样本回测指标',allocMetricRows(data,chosen),['strategy','sample_set','months','annual_return','annual_volatility','sharpe','calmar','max_drawdown','positive_month_rate','total_return'])+tableHTML('候选模型审计 · PBO '+allocPct(audit.pbo_cscv)+' / DSR '+allocPct(audit.deflated_sharpe_probability),leaderboard,['strategy','status','train_sharpe','validation_sharpe','test_sharpe_report_only','validation_drawdown','turnover'])+tableHTML('测试集成本敏感性',costs,['transaction_cost_bps','annual_return','annual_volatility','sharpe','max_drawdown']));
    $('alloc-backtest-window').value=S.allocation.backtestWindow;$('alloc-nav-mode').value=S.allocation.navMode;allocDrawBacktest(c1,c2,c3,data,chosen,S.allocation.backtestWindow,S.allocation.navMode);
    $('alloc-backtest-strategies').onchange=function(){const values=Array.from(this.selectedOptions).map(function(o){return o.value;}).slice(0,5);S.allocation.backtest=values.length?values:['recommended'];allocationBacktest();};$('alloc-backtest-window').onchange=function(){S.allocation.backtestWindow=this.value;allocationBacktest();};$('alloc-nav-mode').onchange=function(){S.allocation.navMode=this.value;allocationBacktest();};$('alloc-gpt-report').onclick=allocationGenerateReport;
  }
  async function renderAllocation(view){if(view==='cycle')return await allocationCycle();if(view==='strategy')return await allocationStrategy();if(view==='backtest')return await allocationBacktest();return await allocationHome();}


  /* Portfolio optimization r42: exact five-page research-to-production contract. */
  S.portfolio=S.portfolio||{snapshot:null,assetType:'ETF',riskFamily:'全部',constraintCategory:'全部',parameterGroup:'全部',strategies:['selected','equal_weight','inverse_volatility','hrp'],window:'full'};
  VIEW_BREADCRUMBS.portfolio={title:'组合优化',views:{home:'主页',pool:'资产池',risk:'风险约束',solve:'优化求解',backtest:'组合回测'}};
  Object.assign(HEAD,{
    'portfolio:home':['组合优化主页','研究状态、五层流程、当前权重与晋级门禁'],
    'portfolio:pool':['资产池','个股、行业、ETF、权益基金与指数的多维画像'],
    'portfolio:risk':['风险约束','风险模型、目标函数、硬软约束与参数注册'],
    'portfolio:solve':['优化求解','训练验证选型、多求解器路由与可行解审计'],
    'portfolio:backtest':['组合回测','封闭测试、成本、压力、回撤与稳健性审计']
  });
  Object.assign(COL,{
    asset_type:'资产类型',group:'风险袖套',observations:'有效观测',annual_return_1y:'近一年年化收益',annual_volatility_1y:'近一年年化波动',sharpe_1y:'近一年夏普',downside_volatility_1y:'下行波动',max_drawdown_3y:'近三年最大回撤',daily_cvar_95:'日度CVaR 95%',average_amount:'日均成交额',target_weight:'目标权重',expected_return:'预期收益',
    family:'风险家族',model:'模型',form:'数学形式',use:'适用场景',category:'约束分类',expression:'表达式',tunable:'可调',layer:'模型层',output:'输出',weight_authority:'权重权限',candidate_id:'候选编号',covariance_method:'协方差',expected_return_method:'预期收益模型',lookback_days:'回看天数',risk_aversion:'风险厌恶',turnover_l2:'换手L2惩罚',turnover_l1:'换手L1惩罚',position_cap:'单资产上限',turnover_cap:'换手上限',
    train_sharpe:'训练夏普',validation_sharpe:'验证夏普',validation_score:'验证得分',validation_max_drawdown:'验证最大回撤',sample_set:'样本',annual_excess_return:'年化超额',information_ratio:'信息比率',annual_turnover:'年化换手',solver:'求解器',median_ms:'中位耗时(ms)',iterations:'迭代次数',max_constraint_violation:'最大约束残差',slack:'松弛量',bound:'边界',scenario:'压力情景',benchmark_return:'基准收益',cost_bps:'单边成本(bp)',period_return:'月收益',transaction_cost:'交易成本'
  });
  const PO_TYPE_COLORS={'ETF':'#b42318','个股':'#2f75b5','行业':'#168a47','权益基金':'#c46a08','指数':'#7030a0'};
  const PO_GROUP_CN={bond_cash:'债券/现金',broad_equity:'宽基权益',sector_equity:'行业权益',commodity:'商品',overseas_equity:'海外权益'};
  const PO_STRATEGY_CN={selected:'验证集入选稳健QP',equal_weight:'等权基准',inverse_volatility:'逆波动基线',hrp:'HRP基线'};
  const PO_SAMPLE_CN={train:'训练集',validation:'验证集',test:'测试集'};
  const PO_SAMPLE_COLOR={train:'rgba(102,112,133,.07)',validation:'rgba(196,106,8,.08)',test:'rgba(22,138,71,.07)'};
  function poPct(v,d){const n=Number(v);return Number.isFinite(n)?fmt(n*100,d==null?2:d)+'%':'--';}
  function poNum(v,d){const n=Number(v);return Number.isFinite(n)?fmt(n,d==null?3:d):'--';}
  function poDate(v){const x=String(v||'').replace(/\D/g,'');return x.length>=8?x.slice(0,4)+'-'+x.slice(4,6)+'-'+x.slice(6,8):String(v||'--');}
  function poUnique(rows,key){return Array.from(new Set(arr(rows).map(function(r){return r[key];}).filter(Boolean)));}
  function poTableHTML(title,rows,cols){rows=arr(rows).slice(0,220);return '<section class="table-panel"><div class="panel-header"><div><h3>'+esc(title)+'</h3><p>'+rows.length+' 行</p></div></div><div class="table-scroll"><table class="data-table"><thead><tr>'+cols.map(function(c){return '<th>'+esc(COL[c]||c)+'</th>';}).join('')+'</tr></thead><tbody>'+rows.map(function(row){return '<tr>'+cols.map(function(c){return cell(row,c);}).join('')+'</tr>';}).join('')+'</tbody></table></div></section>';}
  async function needPortfolio(){if(S.portfolio.snapshot)return S.portfolio.snapshot;S.portfolio.snapshot=await api('/api/portfolio/snapshot');return S.portfolio.snapshot;}
  function poStamp(data){setText('as-of',poDate(data.data_as_of));setText('generated-at',String(data.generated_at||'--').replace('T',' ').replace('Z',''));}
  function poMetricRows(data,keys){const out=[];arr(keys).forEach(function(key){const strategy=obj(obj(data.backtest).strategies)[key];Object.entries(obj(strategy.metrics)).forEach(function(item){const m=obj(item[1]);out.push({strategy:PO_STRATEGY_CN[key]||key,sample_set:PO_SAMPLE_CN[item[0]]||item[0],months:m.months,annual_return:poPct(m.annual_return),annual_volatility:poPct(m.annual_volatility),sharpe:poNum(m.sharpe),max_drawdown:poPct(m.max_drawdown),calmar:poNum(m.calmar),annual_excess_return:poPct(m.annual_excess_return),information_ratio:poNum(m.information_ratio),annual_turnover:poPct(m.annual_turnover),cost_drag:poPct(m.cost_drag)});});});return out;}
  function poCurrentRows(data){return arr(obj(data.home).current_weights).map(function(r){return {code:r.code,name:r.name,group:PO_GROUP_CN[r.group]||r.group,target_weight:poPct(r.weight),expected_return:poPct(r.expected_return),annual_volatility_1y:poPct(r.annual_volatility),risk_contribution:poPct(r.risk_contribution)};});}
  function poWeightCharts(weightId,riskId,data){const rows=arr(obj(data.home).current_weights),labels=rows.map(function(r){return r.name||r.code;});plot(weightId,[{type:'pie',hole:.58,labels:labels,values:rows.map(function(r){return Number(r.weight)||0;}),textinfo:'none',hovertemplate:'%{label}<br>权重 %{percent:.2%}<extra></extra>',marker:{colors:CHART_PALETTE}}],{height:350,showlegend:true,legend:{orientation:'h',y:-.18,font:{size:10}},margin:{l:20,r:20,t:12,b:80},annotations:[{text:'当前权重',showarrow:false,font:{size:14,color:'#344054'}}]});plot(riskId,[{type:'bar',x:labels,y:rows.map(function(r){return Number(r.risk_contribution)*100;}),marker:{color:rows.map(function(r){return Number(r.risk_contribution)>=0?'#b42318':'#168a47';})},hovertemplate:'%{x}<br>风险贡献 %{y:.2f}%<extra></extra>'}],{height:350,showlegend:false,xaxis:{tickangle:-32,showgrid:false},yaxis:{title:'风险贡献（%）',zeroline:true,zerolinecolor:'#98a2b3'}});}
  async function portfolioHome(){
    const data=await needPortfolio(),home=obj(data.home),spec=obj(home.selected_candidate),solver=obj(home.selected_solver),gate=obj(home.promotion_gate),test=obj(obj(obj(data.backtest).strategies).selected.metrics).test,weights=arr(home.current_weights);poStamp(data);header('主页','','组合优化');
    conclusion('当前方案为 '+esc(poPlanName(spec))+'；封闭测试夏普 '+poNum(test.sharpe)+'、最大回撤 '+poPct(test.max_drawdown)+'。PBO '+poPct(obj(obj(data.optimization).pbo_cscv).pbo)+'、DSR '+poPct(obj(data.optimization).deflated_sharpe_probability)+' 未满足晋级门槛，当前严格标记为研究候选。');
    const c1=pid('po'),c2=pid('po'),fast=Math.min.apply(null,arr(obj(data.optimization).solver_benchmark).map(function(r){return Number(r.median_ms)||Infinity;}));
    const pipeline=arr(home.pipeline).map(function(r){return {stage:r.stage,status:r.status==='passed'?'通过':r.status==='conditional'?'条件性':'未通过',detail:r.detail};});
    const gates=[{parameter:'测试集参与选模',value:gate.test_used_for_selection?'是':'否',status:gate.test_used_for_selection?'失败':'通过'},{parameter:'PBO < 20%',value:poPct(obj(obj(data.optimization).pbo_cscv).pbo),status:gate.pbo_passed?'通过':'未通过'},{parameter:'DSR ≥ 95%',value:poPct(obj(data.optimization).deflated_sharpe_probability),status:gate.dsr_passed?'通过':'未通过'},{parameter:'影子运行',value:'要求 '+gate.shadow_months_required+' 个月',status:'待执行'}];
    root(cardHTML([{label:'优化资产',value:weights.length,unit:'只',as_of:data.data_as_of},{label:'预声明候选',value:obj(data.method).candidate_count,unit:'组'},{label:'最快中位求解',value:fast,unit:'ms'},{label:'测试夏普（仅报告）',value:test.sharpe,as_of:'2023–'+String(data.data_as_of).slice(0,4)}])+'<div class="panel-grid">'+panel(c1,'当前资本权重','十五只ETF、五类风险袖套')+panel(c2,'边际风险贡献','允许分散资产出现负贡献')+'</div>'+poTableHTML('五层执行流水线',pipeline,['stage','status','detail'])+poTableHTML('训练 / 验证 / 封闭测试指标',poMetricRows(data,['selected']),['strategy','sample_set','months','annual_return','annual_volatility','sharpe','max_drawdown','calmar','annual_excess_return','information_ratio','annual_turnover'])+poTableHTML('研究转实盘门禁',gates,['parameter','value','status'])+poTableHTML('当前权重明细',poCurrentRows(data),['code','name','group','target_weight','expected_return','annual_volatility_1y','risk_contribution']));poWeightCharts(c1,c2,data);
  }
  function poProfileRows(data,type){return arr(obj(data.asset_pool).profiles).filter(function(r){return r.asset_type===type;}).map(function(r){return {asset_type:r.asset_type,code:r.code,name:r.name,group:PO_GROUP_CN[r.group]||r.group,observations:r.observations,annual_return_1y:poPct(r.annual_return_1y),annual_volatility_1y:poPct(r.annual_volatility_1y),sharpe_1y:poNum(r.sharpe_1y),downside_volatility_1y:poPct(r.downside_volatility_1y),max_drawdown_3y:poPct(r.max_drawdown_3y),daily_cvar_95:poPct(r.daily_cvar_95),average_amount:Number(r.average_amount)?fmt(Number(r.average_amount)/1e8,2)+'亿':'--',score:r.score==null?'--':poNum(r.score),target_weight:r.target_weight==null?'--':poPct(r.target_weight)};});}
  function poAssetNav(id,series,type){const traces=arr(series).slice(0,8).map(function(s,index){const points=arr(s.data);return {type:'scatter',mode:'lines',name:s.name||s.code,x:points.map(function(p){return poDate(p.date);}),y:points.map(function(p){return Number(p.value);}),line:{width:2,color:index===0?PO_TYPE_COLORS[type]:CHART_PALETTE[index%CHART_PALETTE.length]},hovertemplate:'%{x}<br>%{y:.3f}<extra>'+esc(s.name||s.code)+'</extra>'};});plot(id,traces,{height:390,hovermode:'x unified',xaxis:{type:'date',rangeslider:{visible:true,thickness:.06}},yaxis:{title:'复权净值'},legend:{orientation:'h',y:-.28}});}
  function poNormalize(values,invert){const nums=values.map(Number),lo=Math.min.apply(null,nums),hi=Math.max.apply(null,nums);return nums.map(function(v){let z=hi>lo?(v-lo)/(hi-lo):.5;if(invert)z=1-z;return 20+80*z;});}
  function poRadar(id,profiles){const top=arr(profiles).slice().sort(function(a,b){return Number(b.sharpe_1y)-Number(a.sharpe_1y);}).slice(0,5),ret=poNormalize(top.map(function(r){return r.annual_return_1y;}),false),vol=poNormalize(top.map(function(r){return r.annual_volatility_1y;}),true),sh=poNormalize(top.map(function(r){return r.sharpe_1y;}),false),dd=poNormalize(top.map(function(r){return Math.abs(r.max_drawdown_3y);}),true),liq=poNormalize(top.map(function(r){return Math.log1p(Number(r.average_amount)||0);}),false);plot(id,top.map(function(r,i){return {type:'scatterpolar',name:r.name||r.code,r:[ret[i],vol[i],sh[i],dd[i],liq[i],ret[i]],theta:['收益','低波','夏普','回撤控制','流动性','收益'],fill:'toself',opacity:.42};}),{height:380,polar:{radialaxis:{visible:true,range:[0,100]}},legend:{orientation:'h',y:-.2},margin:{l:40,r:40,t:20,b:80}});}
  async function portfolioPool(){
    const data=await needPortfolio(),type=S.portfolio.assetType,profiles=arr(obj(data.asset_pool).profiles).filter(function(r){return r.asset_type===type;}),nav=arr(obj(obj(data.asset_pool).nav_series)[type]),corr=obj(obj(data.asset_pool).correlation)[type],c1=pid('pp'),c2=pid('pp'),c3=pid('pp'),c4=pid('pp');poStamp(data);header('资产池','个股、行业、ETF、权益基金与指数的多维画像','组合优化');conclusion(type+' 共 '+profiles.length+' 个标的；本页同步展示复权净值、收益—风险、相关性、下行风险、流动性与可选目标权重。');
    const controls='<section class="control-card"><div class="control-grid"><label>资产类型<select id="po-asset-type">'+['ETF','个股','行业','权益基金','指数'].map(function(k){return '<option value="'+k+'"'+(k===type?' selected':'')+'>'+k+'</option>';}).join('')+'</select></label><div class="control-readout">资产数量<strong>'+profiles.length+'</strong></div><div class="control-readout">净值曲线<strong>'+nav.length+'</strong></div></div></section>';
    root(controls+'<div class="panel-grid">'+panel(c1,type+'复权净值','最多展示八条，完整明细见下表',true)+panel(c2,'收益—风险画像','横轴年化波动，纵轴年化收益')+panel(c3,'相关性热力图','同类标的近端相关结构')+panel(c4,'多维能力雷达','收益、低波、夏普、回撤控制、流动性')+'</div>'+poTableHTML(type+'资产画像',poProfileRows(data,type),['asset_type','code','name','group','observations','annual_return_1y','annual_volatility_1y','sharpe_1y','downside_volatility_1y','max_drawdown_3y','daily_cvar_95','average_amount','score','target_weight']));
    poAssetNav(c1,nav,type);plot(c2,[{type:'scatter',mode:'markers+text',x:profiles.map(function(r){return Number(r.annual_volatility_1y)*100;}),y:profiles.map(function(r){return Number(r.annual_return_1y)*100;}),text:profiles.map(function(r){return r.name||r.code;}),textposition:'top center',marker:{size:profiles.map(function(r){return Math.max(9,Math.min(24,8+Math.log10(Math.max(Number(r.average_amount)||1,1))));}),color:PO_TYPE_COLORS[type],opacity:.76},hovertemplate:'%{text}<br>波动 %{x:.2f}%<br>收益 %{y:.2f}%<extra></extra>'}],{height:380,showlegend:false,xaxis:{title:'年化波动（%）'},yaxis:{title:'年化收益（%）'}});plot(c3,corr&&arr(corr.matrix).length?[{type:'heatmap',x:corr.labels,y:corr.labels,z:corr.matrix,zmin:-1,zmax:1,zmid:0,colorscale:[[0,'#168a47'],[.5,'#ffffff'],[1,'#b42318']],hovertemplate:'%{y} / %{x}<br>相关系数 %{z:.3f}<extra></extra>'}]:[],{height:380,xaxis:{tickangle:-45},yaxis:{automargin:true},margin:{l:90,r:20,t:15,b:90}});poRadar(c4,profiles);$('po-asset-type').onchange=function(){S.portfolio.assetType=this.value;portfolioPool();};
  }
  async function portfolioRisk(){
    const data=await needPortfolio(),risk=obj(data.risk_constraints),models=arr(risk.risk_models),constraints=arr(risk.constraints),parameters=arr(risk.parameters),families=poUnique(models,'family'),categories=poUnique(constraints,'category'),groups=poUnique(parameters,'group'),mf=S.portfolio.riskFamily,cc=S.portfolio.constraintCategory,pg=S.portfolio.parameterGroup,shownModels=mf==='全部'?models:models.filter(function(r){return r.family===mf;}),shownConstraints=cc==='全部'?constraints:constraints.filter(function(r){return r.category===cc;}),shownParameters=pg==='全部'?parameters:parameters.filter(function(r){return r.group===pg;}),cov=obj(risk.covariance),c1=pid('pr'),c2=pid('pr'),c3=pid('pr'),c4=pid('pr');poStamp(data);header('风险约束','风险模型、目标函数、硬软约束与参数注册','组合优化');conclusion('已注册 '+models.length+' 个风险模型、'+constraints.length+' 条约束、'+parameters.length+' 个参数；当前生产解使用正定化协方差、资本/风险袖套/集中度/换手约束，研究模型不得直接越权输出权重。');
    const sel=function(id,label,values,selected){return '<label>'+label+'<select id="'+id+'"><option>全部</option>'+values.map(function(v){return '<option'+(v===selected?' selected':'')+'>'+esc(v)+'</option>';}).join('')+'</select></label>';};
    const controls='<section class="control-card"><div class="control-grid">'+sel('po-risk-family','风险模型家族',families,mf)+sel('po-constraint-category','约束类别',categories,cc)+sel('po-parameter-group','参数组',groups,pg)+'<div class="control-readout">协方差条件数<strong>'+poNum(obj(cov).condition_number,1)+'</strong></div></div></section>',familyRows=families.map(function(f){return {label:f,value:models.filter(function(r){return r.family===f;}).length};}),rc=arr(risk.risk_contribution);
    root(controls+'<div class="panel-grid">'+panel(c1,'当前协方差相关结构','十五只优化ETF')+panel(c2,'协方差特征值谱','PSD修复后由大到小')+panel(c3,'当前风险贡献','正值为风险来源，负值为分散贡献')+panel(c4,'风险模型家族覆盖','常见与非常见模型登记数量')+'</div>'+poTableHTML('风险模型目录 · '+shownModels.length,shownModels,['family','model','form','status','use'])+poTableHTML('约束目录 · '+shownConstraints.length,shownConstraints,['category','constraint','expression','form','status'])+poTableHTML('参数注册表 · '+shownParameters.length,shownParameters,['group','parameter','value','status','tunable']));
    plot(c1,[{type:'heatmap',x:cov.labels,y:cov.labels,z:cov.correlation,zmin:-1,zmax:1,zmid:0,colorscale:[[0,'#168a47'],[.5,'#fff'],[1,'#b42318']]}],{height:390,xaxis:{tickangle:-45},yaxis:{automargin:true},margin:{l:90,r:20,t:15,b:90}});const ev=arr(cov.eigenvalues).slice().sort(function(a,b){return b-a;});plot(c2,[{type:'bar',x:ev.map(function(_,i){return i+1;}),y:ev,marker:{color:'#2f75b5'}}],{height:390,yaxis:{type:'log',title:'特征值（对数轴）'},xaxis:{title:'排序'}});plot(c3,[{type:'bar',x:rc.map(function(r){return r.name||r.code;}),y:rc.map(function(r){return Number(r.risk_contribution)*100;}),marker:{color:rc.map(function(r){return Number(r.risk_contribution)>=0?'#b42318':'#168a47';})}}],{height:390,xaxis:{tickangle:-35},yaxis:{title:'风险贡献（%）'}});plot(c4,[{type:'bar',orientation:'h',x:familyRows.map(function(r){return r.value;}),y:familyRows.map(function(r){return r.label;}),marker:{color:'#c46a08'},text:familyRows.map(function(r){return r.value;}),textposition:'auto'}],{height:390,xaxis:{title:'模型数'},yaxis:{automargin:true}});$('po-risk-family').onchange=function(){S.portfolio.riskFamily=this.value;portfolioRisk();};$('po-constraint-category').onchange=function(){S.portfolio.constraintCategory=this.value;portfolioRisk();};$('po-parameter-group').onchange=function(){S.portfolio.parameterGroup=this.value;portfolioRisk();};
  }
  async function portfolioSolve(){
    const data=await needPortfolio(),opt=obj(data.optimization),spec=obj(opt.selected_spec),leaders=arr(opt.leaderboard),solvers=arr(opt.solver_benchmark),weights=arr(opt.current_weights),slack=arr(obj(opt.constraint_slack).rows),selected=leaders.find(function(r){return r.candidate_id===spec.candidate_id;})||{},fast=solvers.slice().sort(function(a,b){return Number(a.median_ms)-Number(b.median_ms);})[0]||{},c1=pid('ps'),c2=pid('ps'),c3=pid('ps'),c4=pid('ps');poStamp(data);header('优化求解','训练验证选型、多求解器路由与可行解审计','组合优化');conclusion('96 组候选仅用训练集筛出 12 组，再由验证集固定 '+esc(spec.candidate_id||'--')+'；测试集不参与调参。当前最快基准为 '+esc(fast.solver||'--')+' '+poNum(fast.median_ms,2)+'ms，最大残差 '+Number(obj(opt.constraint_slack).max_violation||0).toExponential(2)+'。');
    const solverRows=solvers.map(function(r){return {solver:r.solver,status:r.status,median_ms:poNum(r.median_ms,3),iterations:r.iterations,max_constraint_violation:r.max_constraint_violation==null?'--':Number(r.max_constraint_violation).toExponential(2)};}),leaderRows=leaders.map(function(r){return {candidate_id:r.candidate_id,status:r.status,covariance_method:r.covariance_method,expected_return_method:r.expected_return_method,lookback_days:r.lookback_days,risk_aversion:r.risk_aversion,turnover_l2:r.turnover_l2,position_cap:poPct(r.position_cap),train_sharpe:poNum(r.train_sharpe),validation_sharpe:poNum(r.validation_sharpe),validation_score:poNum(r.validation_score),validation_max_drawdown:poPct(r.validation_max_drawdown)};}),slackRows=slack.map(function(r){return {constraint:r.constraint,value:poNum(r.value,6),bound:poNum(r.bound,6),slack:poNum(r.slack,6),status:r.status};});
    root(cardHTML([{label:'风险厌恶',value:spec.risk_aversion,as_of:'验证集固定'},{label:'验证夏普',value:selected.validation_sharpe,as_of:'2021–2022'},{label:'最快中位求解',value:fast.median_ms,unit:'ms'},{label:'最大约束残差',value:obj(opt.constraint_slack).max_violation,as_of:'当前解'}])+'<div class="panel-grid">'+panel(c1,'有效前沿','预期波动—收益与风险厌恶系数')+panel(c2,'当前权重与风险贡献','资本权重对比边际风险贡献')+panel(c3,'约束松弛量','越接近零越接近边界')+panel(c4,'训练—验证候选分布','不显示测试结果，避免反向调参')+'</div>'+poTableHTML('求解器对拍',solverRows,['solver','status','median_ms','iterations','max_constraint_violation'])+poTableHTML('候选模型排行榜',leaderRows,['candidate_id','status','covariance_method','expected_return_method','lookback_days','risk_aversion','turnover_l2','position_cap','train_sharpe','validation_sharpe','validation_score','validation_max_drawdown'])+poTableHTML('当前解约束审计',slackRows,['constraint','value','bound','slack','status'])+poTableHTML('模型状态',arr(opt.model_registry),['layer','model','status','output','weight_authority']));
    const frontier=arr(opt.efficient_frontier);plot(c1,[{type:'scatter',mode:'lines+markers',x:frontier.map(function(r){return Number(r.volatility)*100;}),y:frontier.map(function(r){return Number(r.expected_return)*100;}),text:frontier.map(function(r){return 'λ='+poNum(r.risk_aversion,2);}),marker:{size:9,color:frontier.map(function(r){return Number(r.risk_aversion);}),colorscale:'Portland',showscale:true,colorbar:{title:'λ'}},hovertemplate:'%{text}<br>波动 %{x:.2f}%<br>收益 %{y:.2f}%<extra></extra>'}],{height:390,xaxis:{title:'预期波动（%）'},yaxis:{title:'预期收益（%）'}});plot(c2,[{type:'bar',name:'资本权重',x:weights.map(function(r){return r.name||r.code;}),y:weights.map(function(r){return Number(r.weight)*100;})},{type:'bar',name:'风险贡献',x:weights.map(function(r){return r.name||r.code;}),y:weights.map(function(r){return Number(r.risk_contribution)*100;})}],{height:390,barmode:'group',xaxis:{tickangle:-35},yaxis:{title:'比例（%）'},legend:{orientation:'h',y:-.28}});plot(c3,[{type:'bar',orientation:'h',x:slack.map(function(r){return Number(r.slack);}),y:slack.map(function(r){return r.constraint;}),marker:{color:slack.map(function(r){return Number(r.slack)<.001?'#b42318':'#2f75b5';})}}],{height:390,xaxis:{title:'松弛量'},yaxis:{automargin:true}});plot(c4,[{type:'scatter',mode:'markers',x:leaders.map(function(r){return Number(r.train_sharpe);}),y:leaders.map(function(r){return Number(r.validation_sharpe);}),text:leaders.map(function(r){return r.candidate_id;}),marker:{size:leaders.map(function(r){return r.candidate_id===spec.candidate_id?16:9;}),color:leaders.map(function(r){return r.candidate_id===spec.candidate_id?'#b42318':'#2f75b5';}),opacity:.78},hovertemplate:'%{text}<br>训练 %{x:.3f}<br>验证 %{y:.3f}<extra></extra>'}],{height:390,xaxis:{title:'训练夏普'},yaxis:{title:'验证夏普'},shapes:[{type:'line',x0:-2,x1:3,y0:-2,y1:3,line:{dash:'dash',color:'#98a2b3'}}]});
  }
  function poWindowRows(rows,windowName){if(windowName==='3y')return rows.slice(-36);if(windowName==='5y')return rows.slice(-60);return rows;}
  function poSampleShapes(rows){const shapes=[],annotations=[];if(!rows.length)return {shapes:shapes,annotations:annotations};let start=0;for(let i=1;i<=rows.length;i++){if(i===rows.length||rows[i].sample_set!==rows[start].sample_set){const sample=rows[start].sample_set;shapes.push({type:'rect',xref:'x',yref:'paper',x0:poDate(rows[start].date),x1:poDate(rows[Math.min(i,rows.length-1)].date),y0:0,y1:1,fillcolor:PO_SAMPLE_COLOR[sample]||'rgba(0,0,0,.03)',line:{width:0},layer:'below'});annotations.push({xref:'x',yref:'paper',x:poDate(rows[start].date),y:1.03,text:PO_SAMPLE_CN[sample]||sample,showarrow:false,xanchor:'left',font:{size:10,color:'#667085'}});start=i;}}return {shapes:shapes,annotations:annotations};}
  function poDrawBacktest(navId,ddId,rollId,turnId,heatId,data,keys,windowName){let baseRows=[];const navTraces=[],ddTraces=[],rollTraces=[];keys.forEach(function(key,index){const rows=poWindowRows(arr(obj(obj(obj(data.backtest).strategies)[key]).nav),windowName);if(!baseRows.length)baseRows=rows;const base=rows.length?Number(rows[0].nav):1;navTraces.push({type:'scatter',mode:'lines',name:PO_STRATEGY_CN[key],x:rows.map(function(r){return poDate(r.date);}),y:rows.map(function(r){return Number(r.nav)/base;}),line:{width:key==='selected'?2.8:1.7,color:key==='selected'?'#b42318':CHART_PALETTE[(index+1)%CHART_PALETTE.length]}});ddTraces.push({type:'scatter',mode:'lines',name:PO_STRATEGY_CN[key],x:rows.map(function(r){return poDate(r.date);}),y:rows.map(function(r){return Number(r.drawdown)*100;}),line:{width:key==='selected'?2.4:1.5}});rollTraces.push({type:'scatter',mode:'lines',name:PO_STRATEGY_CN[key],x:rows.map(function(r){return poDate(r.date);}),y:rows.map(function(r){return r.rolling_sharpe_12m;}),connectgaps:false,line:{width:key==='selected'?2.4:1.5}});});const deco=poSampleShapes(baseRows);plot(navId,navTraces,{height:410,hovermode:'x unified',xaxis:{type:'date',rangeslider:{visible:true,thickness:.06}},yaxis:{title:'成本后净值'},legend:{orientation:'h',y:-.28},shapes:deco.shapes,annotations:deco.annotations});plot(ddId,ddTraces,{height:330,hovermode:'x unified',xaxis:{type:'date'},yaxis:{title:'回撤（%）'},legend:{orientation:'h',y:-.26},shapes:deco.shapes,annotations:deco.annotations});plot(rollId,rollTraces,{height:330,hovermode:'x unified',xaxis:{type:'date'},yaxis:{title:'12月滚动夏普'},legend:{orientation:'h',y:-.26},shapes:deco.shapes,annotations:deco.annotations});const selected=poWindowRows(arr(obj(obj(obj(data.backtest).strategies).selected).nav),windowName);plot(turnId,[{type:'bar',name:'换手率',x:selected.map(function(r){return poDate(r.date);}),y:selected.map(function(r){return Number(r.turnover)*100;}),marker:{color:'#2f75b5'}},{type:'scatter',mode:'lines',name:'交易成本',x:selected.map(function(r){return poDate(r.date);}),y:selected.map(function(r){return Number(r.transaction_cost)*100;}),yaxis:'y2',line:{color:'#b42318',width:2}}],{height:330,xaxis:{type:'date'},yaxis:{title:'换手率（%）'},yaxis2:{title:'成本（%）',overlaying:'y',side:'right'},legend:{orientation:'h',y:-.26}});const months=['01','02','03','04','05','06','07','08','09','10','11','12'],years=poUnique(selected.map(function(r){return {year:String(r.date).slice(0,4)};}),'year').sort();const map=new Map(selected.map(function(r){return [String(r.date).slice(0,6),Number(r.period_return)*100];}));plot(heatId,[{type:'heatmap',x:years,y:months,z:months.map(function(m){return years.map(function(y){return map.has(y+m)?map.get(y+m):null;});}),zmid:0,colorscale:[[0,'#168a47'],[.5,'#fff'],[1,'#b42318']],texttemplate:'%{z:.1f}',hovertemplate:'%{x}-%{y}<br>%{z:.2f}%<extra></extra>'}],{height:360,xaxis:{title:'年份'},yaxis:{title:'月份',autorange:'reversed'},margin:{l:55,r:35,t:15,b:55}});}
  async function portfolioBacktest(){
    const data=await needPortfolio(),keys=S.portfolio.strategies.length?S.portfolio.strategies:['selected'],gate=obj(obj(data.backtest).promotion_gate),selectedTest=obj(obj(obj(obj(data.backtest).strategies).selected).metrics).test,c1=pid('pb'),c2=pid('pb'),c3=pid('pb'),c4=pid('pb'),c5=pid('pb');poStamp(data);header('组合回测','封闭测试、成本、压力、回撤与稳健性审计','组合优化');conclusion('封闭测试仅报告：入选组合年化 '+poPct(selectedTest.annual_return)+'、夏普 '+poNum(selectedTest.sharpe)+'、最大回撤 '+poPct(selectedTest.max_drawdown)+'；PBO/DSR 未通过且尚无 12 个月影子记录，因此禁止标记为实盘可用。');
    const controls='<section class="control-card"><div class="control-grid"><label style="grid-column:span 2;">对照策略<select id="po-backtest-strategies" multiple size="4">'+Object.keys(PO_STRATEGY_CN).map(function(k){return '<option value="'+k+'"'+(keys.includes(k)?' selected':'')+'>'+PO_STRATEGY_CN[k]+'</option>';}).join('')+'</select></label><label>观察窗口<select id="po-backtest-window"><option value="full">全部样本</option><option value="5y">近5年</option><option value="3y">近3年</option></select></label><div class="control-readout">晋级状态<strong>研究候选</strong></div></div></section>',costs=arr(obj(data.backtest).cost_sensitivity_test).map(function(r){return {cost_bps:r.cost_bps,annual_return:poPct(r.annual_return),annual_volatility:poPct(r.annual_volatility),sharpe:poNum(r.sharpe),max_drawdown:poPct(r.max_drawdown),annual_excess_return:poPct(r.annual_excess_return),information_ratio:poNum(r.information_ratio)};}),stress=arr(obj(data.backtest).stress_scenarios).map(function(r){return {scenario:r.scenario,start:poDate(r.start),end:poDate(r.end),total_return:poPct(r.return),max_drawdown:poPct(r.max_drawdown),benchmark_return:poPct(r.benchmark_return)};}),gateRows=[{parameter:'研究状态',value:gate.status,status:'未晋级'},{parameter:'测试集用于选模',value:gate.test_used_for_selection?'是':'否',status:gate.test_used_for_selection?'失败':'通过'},{parameter:'PBO门槛',value:gate.pbo_passed?'通过':'未通过',status:gate.pbo_passed?'通过':'未通过'},{parameter:'DSR门槛',value:gate.dsr_passed?'通过':'未通过',status:gate.dsr_passed?'通过':'未通过'},{parameter:'影子运行',value:'至少 '+gate.shadow_months_required+' 个月',status:'待执行'}];
    root(controls+panel(c1,'成本后策略净值','背景依次为训练、验证、封闭测试',true)+'<div class="panel-grid">'+panel(c2,'动态回撤','与净值同口径')+panel(c3,'12月滚动夏普','不足12个月留空')+panel(c4,'换手与交易成本','月末调仓、单边10bp')+panel(c5,'入选组合月收益热力图','红涨绿跌')+'</div>'+poTableHTML('分样本组合指标',poMetricRows(data,keys),['strategy','sample_set','months','annual_return','annual_volatility','sharpe','max_drawdown','calmar','annual_excess_return','information_ratio','annual_turnover','cost_drag'])+poTableHTML('成本敏感性 · 测试集仅报告',costs,['cost_bps','annual_return','annual_volatility','sharpe','max_drawdown','annual_excess_return','information_ratio'])+poTableHTML('历史压力情景',stress,['scenario','start','end','total_return','max_drawdown','benchmark_return'])+poTableHTML('研究转实盘门禁',gateRows,['parameter','value','status']));$('po-backtest-window').value=S.portfolio.window;poDrawBacktest(c1,c2,c3,c4,c5,data,keys,S.portfolio.window);$('po-backtest-strategies').onchange=function(){const values=Array.from(this.selectedOptions).map(function(o){return o.value;});S.portfolio.strategies=values.length?values:['selected'];portfolioBacktest();};$('po-backtest-window').onchange=function(){S.portfolio.window=this.value;portfolioBacktest();};
  }
  async function renderPortfolio(view){if(view==='pool')return await portfolioPool();if(view==='risk')return await portfolioRisk();if(view==='solve')return await portfolioSolve();if(view==='backtest')return await portfolioBacktest();return await portfolioHome();}

  S.liquidity=S.liquidity||{snapshot:null};
  HEAD['liquidity:home']=['资金面跟踪主页','A股七类资金与全球美元流动性统一监测'];
  HEAD['liquidity:retail']=['散户资金','小单流、开户与投资者参与度'];
  HEAD['liquidity:public']=['公募基金','新发、报会、仓位与清算'];
  HEAD['liquidity:etf']=['ETF资金','份额申赎、资金流与结构分解'];
  HEAD['liquidity:margin']=['融资资金','净买入、余额、活跃度与担保结构'];
  HEAD['liquidity:primary']=['一级市场','IPO、定增与可转债融资供给'];
  HEAD['liquidity:private']=['私募基金','仓位、规模与托管策略指数'];
  HEAD['liquidity:foreign']=['外资资金','配置流、A/H分配、陆股通成交与仓位'];

  async function needLiquidity(){
    if(S.liquidity.snapshot)return S.liquidity.snapshot;
    S.liquidity.snapshot=await api('/api/liquidity/snapshot');
    return S.liquidity.snapshot;
  }
  function liquidityLatest(chart){
    const trace=arr(chart.traces)[0],ys=arr(trace&&trace.y),xs=arr(trace&&trace.x);
    return ys.length?{value:ys[ys.length-1],date:xs[xs.length-1]||obj(chart.quality).end}:null;
  }
  function liquidityPanel(chart,id){
    const quality=obj(chart.quality),tall=chart.kind==='category'&&Number(quality.categories)>16,wide=tall;
    const span=chart.kind==='time'?(quality.start+' 至 '+quality.end+' · '+quality.common_observations+' 个共同观测'):(quality.categories+' 个分类');
    return '<section class="chart-panel '+(wide?'wide ':'')+(tall?'is-tall':'')+'"><div class="panel-header"><div><h3>'+esc(chart.title)+'</h3></div></div><div id="'+id+'" class="plot-frame"></div></section>';
  }
  function liquidityAxis(axis){
    axis=obj(axis);return {title:{text:axis.title||''},range:[Number(axis.min),Number(axis.max)],dtick:Number(axis.dtick),showgrid:false,zeroline:true,zerolinecolor:'#d0d5dd',linecolor:'#111827',tickcolor:'#111827',ticks:'outside',tickfont:{size:10},automargin:true};
  }
  function liquidityTimeTrace(trace){
    const common={name:trace.name,x:trace.x,y:trace.y,yaxis:trace.axis==='right'?'y2':'y',hovertemplate:'%{x|%Y-%m-%d}<br>'+esc(trace.name)+' %{y:.2f}<extra></extra>'};
    if(trace.type==='bar')return Object.assign(common,{type:'bar',marker:{color:trace.color_by_sign?arr(trace.y).map(function(v){return Number(v)>=0?'#c00000':'#00b050';}):trace.color,line:{width:0}},opacity:.72});
    return Object.assign(common,{type:'scatter',mode:'lines',fill:trace.type==='area'?'tozeroy':undefined,fillcolor:trace.type==='area'?'rgba(192,0,0,.18)':undefined,line:{color:trace.color,width:1.75,dash:trace.dash||'solid'},connectgaps:false});
  }
  function drawLiquidityChart(id,chart){
    const left=liquidityAxis(obj(chart.axes).left),right=obj(chart.axes).right;
    if(chart.kind==='time'){
      const traces=arr(chart.traces).map(liquidityTimeTrace),layout={height:350,hovermode:'x unified',barmode:'relative',showlegend:traces.length>1,legend:{orientation:'h',y:-.25,x:0,font:{size:10}},xaxis:{type:'date',dtick:obj(chart.x_tick).dtick||'M6',tickformat:'%Y-%m',showgrid:false,linecolor:'#111827',tickcolor:'#111827',ticks:'outside',tickfont:{size:10},automargin:true},yaxis:left,margin:{l:58,r:right?58:24,t:12,b:68}};
      if(right)layout.yaxis2=Object.assign(liquidityAxis(right),{overlaying:'y',side:'right'});
      return plot(id,traces,layout);
    }
    const categories=arr(chart.traces[0]&&chart.traces[0].x),isTall=categories.length>16,series=arr(chart.traces),axis=liquidityAxis(obj(chart.axes).left),traces=[];let average=null;
    series.forEach(function(trace){
      if(isTall&&trace.type==='line'){average=Number(arr(trace.y)[0]);return;}
      const colors=trace.color_by_sign?arr(trace.y).map(function(v){return Number(v)>=0?'#c00000':'#00b050';}):(trace.color||CHART_PALETTE[traces.length]);
      if(isTall)traces.push({type:'bar',orientation:'h',name:trace.name,x:trace.y,y:trace.x,marker:{color:colors},hovertemplate:'%{y}<br>%{x:.2f}<extra></extra>'});
      else if(trace.type==='line')traces.push({type:'scatter',mode:'lines',name:trace.name,x:trace.x,y:trace.y,line:{color:trace.color||'#808080',width:1.75},hovertemplate:'%{x}<br>%{y:.2f}<extra></extra>'});
      else traces.push({type:'bar',name:trace.name,x:trace.x,y:trace.y,marker:{color:colors},hovertemplate:'%{x}<br>%{y:.2f}<extra></extra>'});
    });
    const layout={height:isTall?590:350,showlegend:traces.length>1,legend:{orientation:'h',y:-.25,font:{size:10}},margin:{l:isTall?115:58,r:24,t:12,b:isTall?54:95},shapes:[]};
    if(isTall){layout.xaxis=axis;layout.yaxis={type:'category',autorange:'reversed',showgrid:false,tickfont:{size:10},automargin:true};if(Number.isFinite(average))layout.shapes.push({type:'line',xref:'x',yref:'paper',x0:average,x1:average,y0:0,y1:1,line:{color:'#808080',width:1.5,dash:'dash'}});}
    else{layout.xaxis={type:'category',tickangle:categories.length>10?-35:0,showgrid:false,linecolor:'#111827',ticks:'outside',tickfont:{size:10},automargin:true};layout.yaxis=axis;}
    plot(id,traces,layout);
  }
  function liquidityAuditCards(data,page){ return ''; }
  async function renderLiquidity(view){
    const data=await needLiquidity(),page=obj(obj(data.pages)[view]||obj(data.pages).home),charts=arr(page.charts),ids=charts.map(function(){return pid('liq');});
    header(page.title,page.subtitle,'资金面跟踪');clearConclusion();
    root(liquidityAuditCards(data,page)+'<div class="liquidity-chart-grid">'+charts.map(function(chart,index){return liquidityPanel(chart,ids[index]);}).join('')+'</div>');
    charts.forEach(function(chart,index){drawLiquidityChart(ids[index],chart);});
  }

  function applyNavStatuses(){
    const services=(S.services&&S.services.services)||{};
    const map={home:'board',data:'board',allocation:'allocation',portfolio:'portfolio',index:'index_enhancement',rotation:'rotation',liquidity:'liquidity',technical:'kline',kline:'kline',factorlab:'factor_lab',factor:'factor'};
    document.querySelectorAll('.nav-item').forEach(function(item){
      const prefix=String(item.dataset.target||'').split(':')[0],service=map[prefix],record=services[service]||{};
      item.dataset.status=st(record.status||record.snapshot_status);
    });
    document.querySelectorAll('.nav-group').forEach(function(group){
      const first=group.querySelector('.nav-item');
      group.dataset.status=first?String(first.dataset.status||'failed'):'failed';
    });
  }
  function serviceBadges(){
    const m=(S.services&&S.services.services)||{},n={board:'数据',allocation:'配置',index_enhancement:'增强',portfolio:'组合',rotation:'轮动',liquidity:'资金',kline:'K线',factor_lab:'实验',factor:'因子'};
    const host=$('service-badges');
    if(host)host.innerHTML=Object.keys(n).map(function(k){return '<span class="service-badge '+st((m[k]||{}).status||(m[k]||{}).snapshot_status)+'">'+n[k]+'</span>';}).join('');
    applyNavStatuses();
  }
  async function render(force){
    seq=0;
    const parts=S.active.split(':');
    const external=['index','rotation','factorlab'].includes(parts[0]);
    if(!external&&!force&&showCachedView(S.active))return;
    if(external){
      displayedView=null;
      if(parts[0]==='index'&&window.IndexEnhancement)return await window.IndexEnhancement.render(parts[1]);
      if(parts[0]==='rotation'&&window.IndustryRotation)return await window.IndustryRotation.render(parts[1]);
      if(parts[0]==='factorlab'&&window.FactorLaboratory)return await window.FactorLaboratory.render(parts[1]);
      throw new Error('模块尚未加载：'+parts[0]);
    }
    ensurePane(S.active);
    if(parts[0]==='data')return await renderData(parts[1]);
    if(parts[0]==='allocation')return await renderAllocation(parts[1]);
    if(parts[0]==='portfolio')return await renderPortfolio(parts[1]);
    if(parts[0]==='liquidity')return await renderLiquidity(parts[1]);
    if(parts[0]==='kline')return await renderKline(parts[1]);
    if(parts[0]==='factor')return await renderFactor(parts[1]);
  }

  /* r18: LLM factor research workspace. Keep this block last so stale r6-r9 declarations cannot win. */
  TXT.factorHome='LLM因子挖掘';
  TXT.factorReport='因子检验结果';
  HEAD['factor:home']=['LLM因子挖掘','GPT假设、可执行程序、严格检验与智能变异'];
  HEAD['factor:expression']=['因子表达式','可执行DSL、数学公式与经济构造'];
  HEAD['factor:report']=['因子检验结果','从数据审计到样本外回测的完整证据链'];
  HEAD['factor:score']=['综合打分','后验裁判、失败归因与智能变异'];
  HEAD['factor:memory']=['历史记忆','账号任务、服务器结果与可复载因子'];

  function fmFinite(){
    for(let i=0;i<arguments.length;i++){
      const value=arguments[i];
      if(value!==null&&value!==undefined&&value!==''&&Number.isFinite(Number(value))) return Number(value);
    }
    return null;
  }
  function fmSig(value,scale,suffix){
    const number=fmFinite(value);
    if(number===null) return '--';
    const rendered=new Intl.NumberFormat('zh-CN',{maximumSignificantDigits:2,minimumSignificantDigits:2}).format(number*(scale||1));
    return rendered+(suffix||'');
  }
  function fmPct(value){return fmSig(value,100,'%');}
  function fmNum(value){return fmSig(value,1,'');}
  function fmCount(value){
    const number=fmFinite(value);
    if(number===null) return '--';
    return new Intl.NumberFormat('zh-CN',{maximumSignificantDigits:2}).format(number);
  }
  function fmDate(value){
    const raw=String(value==null?'':value).trim().replace(/\.0$/,'');
    if(/^\d{8}$/.test(raw)) return raw.slice(0,4)+'-'+raw.slice(4,6)+'-'+raw.slice(6,8);
    if(/^\d{6}$/.test(raw)) return raw.slice(0,4)+'-'+raw.slice(4,6)+'-01';
    if(/^\d{4}$/.test(raw)) return raw+'-01-01';
    return raw;
  }
  function fmDateText(value){const date=fmDate(value);return date||'--';}
  function fmDuration(value){
    const seconds=Math.max(0,Number(value)||0);
    if(seconds<60) return fmSig(seconds,1,'秒');
    if(seconds<3600) return fmSig(seconds/60,1,'分钟');
    return fmSig(seconds/3600,1,'小时');
  }
  function fmClamp(value,low,high){return Math.max(low,Math.min(high,Number(value)||0));}
  function fmStatus(value){
    const raw=String(value||'').toLowerCase();
    const map={done:'完成',completed:'完成',running:'运行中',queued:'排队中',failed:'失败',accepted:'通过',rejected:'未通过',healthy:'健康',tail_realization_watch:'尾部兑现观察',research_ready:'研究可用'};
    return map[raw]||valueText(value||'--');
  }
  function fmChannel(value){
    const map={nested_orthogonal_complement_seed:'嵌套正交补充',llm_hypothesis_generation:'GPT假设生成',mcts:'蒙特卡洛树搜索',genetic:'遗传变异',openfe:'自动特征生成',deep_representation:'深度表征',bandit:'强化选择'};
    return map[String(value||'')]||valueText(value||'--');
  }
  function fmState(value){
    const map={down_high_risk:'下行高风险',down_low_risk:'下行低风险',up_high_risk:'上行高风险',up_low_risk:'上行低风险'};
    return map[String(value||'')]||String(value||'--');
  }
  function fmTone(value){
    if(value===true||['done','accepted','healthy','pass','passed'].includes(String(value||'').toLowerCase())) return 'pass';
    if(value===false||['failed','rejected','fail','blocked'].includes(String(value||'').toLowerCase())) return 'fail';
    return 'watch';
  }
  function fmObj(value){return value&&typeof value==='object'&&!Array.isArray(value)?value:{};}
  function fmFirstObj(){for(let i=0;i<arguments.length;i++){const value=fmObj(arguments[i]);if(Object.keys(value).length)return value;}return {};}
  function fmFirstArray(){for(let i=0;i<arguments.length;i++){if(Array.isArray(arguments[i])&&arguments[i].length)return arguments[i];}return [];}
  function fmDropCache(path){try{API_MEMO.delete(apiPath(path));}catch(_){}}

  async function needFactor(force){
    const refresh=!!force||!S.factor.historyLoadedAt||Date.now()-S.factor.historyLoadedAt>30000;
    const nonce=force?('&ts='+Date.now()):'';
    try{
      const tasks=[];
      if(force||!S.factor.status){
        fmDropCache('/api/factor/status');
        const statusPath='/api/factor/status'+(force?'?refresh=1'+nonce:'');
        fmDropCache(statusPath);
        tasks.push(api(statusPath).then(function(payload){S.factor.status=payload;}));
      }
      if(refresh||!S.factor.history){
        fmDropCache('/api/factor/history');
        const historyPath='/api/factor/history'+(force?'?refresh=1'+nonce:'');
        fmDropCache(historyPath);
        tasks.push(api(historyPath).then(function(payload){S.factor.history=payload;S.factor.historyLoadedAt=Date.now();}));
      }
      await Promise.all(tasks);
      return true;
    }catch(error){
      conclusion('因子服务读取失败：'+esc(error.message));
      return false;
    }
  }
  function fRows(){
    const payload=fmObj(S.factor.history), merged=new Map();
    function absorb(row,source){
      row=fmObj(row);const id=String(row.job_id||row.id||'');if(!id)return;
      const prior=merged.get(id)||{job_id:id,_sources:[]};
      Object.keys(row).forEach(function(key){if(row[key]!==null&&row[key]!==undefined&&row[key]!=='')prior[key]=row[key];});
      if(!prior._sources.includes(source))prior._sources.push(source);
      merged.set(id,prior);
    }
    arr(payload.server_runs).forEach(function(row){absorb(row,'服务器');});
    arr(payload.account_history).forEach(function(row){absorb(row,'账号');});
    return Array.from(merged.values()).map(function(row){
      row.source_label=row._sources.join(' + ');row.source=row.source_label;return row;
    }).sort(function(a,b){return String(b.created_at||'').localeCompare(String(a.created_at||''));});
  }
  async function factorDetail(jobId,force){
    await needFactor(false);
    const rows=fRows();
    const wanted=String(jobId||S.factor.selectedJob||(rows[0]&&rows[0].job_id)||'');
    if(!wanted){S.factor.detail=null;return null;}
    if(!force&&S.factor.detail&&String(S.factor.selectedJob)===wanted)return S.factor.detail;
    const basePath='/api/factor/history/'+encodeURIComponent(wanted),path=basePath+(force?'?refresh=1&ts='+Date.now():'');
    fmDropCache(basePath);fmDropCache(path);
    S.factor.detail=await api(path);
    S.factor.selectedJob=wanted;
    S.factor.selectedIndex=0;
    return S.factor.detail;
  }
  function fResult(){return fmObj((S.factor.detail||{}).result||(S.factor.job||{}).result);}
  function reports(){
    const result=fResult(), source=fmFirstArray(result.factor_reports,result.accepted_factors,result.leaderboard), seen=new Set();
    return source.filter(function(row){const key=String((row||{}).factor||(row||{}).name||(row||{}).chinese_name||seen.size);if(seen.has(key))return false;seen.add(key);return true;});
  }
  function selectedFactor(){
    const rows=reports();
    const index=fmClamp(S.factor.selectedIndex||0,0,Math.max(0,rows.length-1));
    S.factor.selectedIndex=index;
    return rows[index]||{};
  }
  function fmHistoryLabel(row){
    return (row.created_at||'未知时间')+' · '+valueText(row.universe||'ALL_A')+' · 通过 '+fmCount(row.accepted_count||0)+' · '+String(row.job_id||'').slice(0,8);
  }
  function fmContextHTML(){
    const history=fRows(), factors=reports(), selectedJob=String(S.factor.selectedJob||''), selectedIndex=Number(S.factor.selectedIndex||0);
    return '<section class="fm-context" aria-label="因子结果选择">'+
      '<label>历史任务<select id="fm-history-select">'+history.map(function(row){return '<option value="'+esc(row.job_id)+'" '+(String(row.job_id)===selectedJob?'selected':'')+'>'+esc(fmHistoryLabel(row))+'</option>';}).join('')+'</select></label>'+
      '<label>当前因子<select id="fm-factor-select">'+factors.map(function(row,index){return '<option value="'+index+'" '+(index===selectedIndex?'selected':'')+'>'+(index+1)+'. '+esc(row.chinese_name||row.name||row.factor||'未命名因子')+' ['+esc(fmStatus(row.status))+']</option>';}).join('')+'</select></label>'+
      '<button id="fm-history-refresh" class="ghost-button" type="button" title="刷新历史记录">刷新</button>'+
      '<div class="fm-context-readout"><span>任务</span><strong>'+esc(selectedJob||'--')+'</strong></div>'+
    '</section>';
  }
  function fmInvalidateFactorViews(){['factor:home','factor:expression','factor:report','factor:score','factor:memory'].forEach(invalidateView);}
  function fmBindContext(){
    const history=$('fm-history-select'),factor=$('fm-factor-select'),refresh=$('fm-history-refresh');
    if(history)history.onchange=async function(){
      await factorDetail(this.value,true);fmInvalidateFactorViews();await render(true);
    };
    if(factor)factor.onchange=async function(){S.factor.selectedIndex=Number(this.value||0);['factor:expression','factor:report','factor:score'].forEach(invalidateView);await render(true);};
    if(refresh)refresh.onclick=async function(){this.disabled=true;try{await needFactor(true);await factorDetail(S.factor.selectedJob||'',true);fmInvalidateFactorViews();await render(true);}finally{this.disabled=false;}};
  }
  const FM_FLOW=[
    ['方法与空间','方法卡、数据、算子、约束'],
    ['GPT假设与程序','候选假设、DSL编译'],
    ['静态审计与计算','因果审计、批量因子值'],
    ['统计检验','初筛、完整单因子检验'],
    ['组合回测与裁判','成本后收益、后验打分'],
    ['归因与智能变异','失败定位、MCTS/遗传改写'],
    ['记忆与停止','经验写回、达标或预算停止']
  ];
  function fmFlowIndex(job){
    const status=String((job||{}).status||'').toLowerCase(),progress=String((job||{}).progress||'');
    if(status==='done'||status==='completed')return FM_FLOW.length;
    if(status==='failed')return FM_FLOW.length;
    if(status==='queued')return 0;
    if(/记忆|停止/.test(progress))return 6;if(/归因|变异|MCTS|遗传/.test(progress))return 5;if(/回测|裁判|打分/.test(progress))return 4;if(/检验|初筛/.test(progress))return 3;if(/审计|计算/.test(progress))return 2;if(/GPT|假设|编译/.test(progress))return 1;
    return status==='running'?1:0;
  }
  function fmFlowHTML(job){
    const index=fmFlowIndex(job),failed=String((job||{}).status)==='failed';
    return '<div class="fm-flow" id="fm-flow">'+FM_FLOW.map(function(step,i){const tone=i<index?'done':i===index&&!failed?'current':failed&&i===index?'failed':'';return '<div class="fm-flow-step '+tone+'"><b>'+(i+1)+'</b><span>'+step[0]+'</span></div>';}).join('')+'</div>';
  }
  function fmJobHTML(job){
    job=fmObj(job);const status=String(job.status||'idle'),progress=fmFlowIndex(job),width=status==='failed'?100:Math.max(3,Math.min(100,progress/FM_FLOW.length*100));
    return '<div class="fm-job-line"><span class="fm-badge '+fmTone(status)+'">'+esc(fmStatus(status))+'</span><strong>'+(esc(job.id||job.job_id||S.factor.selectedJob||'尚未提交'))+'</strong><span>'+esc(job.progress||'等待任务')+'</span><span>耗时 '+fmDuration(job.elapsed_seconds||0)+'</span></div><div class="fm-progress"><i style="width:'+width+'%"></i></div>'+(job.error?'<div class="fm-error">'+esc(job.error)+'</div>':'');
  }
  function fmCandidateCards(rows,limit){
    rows=arr(rows).slice(0,limit||20);
    if(!rows.length)return '<div class="fm-empty">当前任务没有可展示的候选因子。</div>';
    return '<div class="fm-candidate-grid">'+rows.map(function(row,index){return '<button type="button" class="fm-candidate '+(index===Number(S.factor.selectedIndex||0)?'active':'')+'" data-fm-factor="'+index+'"><span class="fm-badge '+fmTone(row.status)+'">'+esc(fmStatus(row.status))+'</span><h4>'+(index+1)+'. '+esc(row.chinese_name||row.name||row.factor||'未命名因子')+'</h4><dl><div><dt>训练RankIC</dt><dd>'+fmNum(row.train_rank_ic)+'</dd></div><div><dt>验证RankIC</dt><dd>'+fmNum(row.valid_rank_ic)+'</dd></div><div><dt>测试RankIC</dt><dd>'+fmNum(row.test_rank_ic)+'</dd></div><div><dt>多空年化</dt><dd>'+fmPct(row.test_long_short_annual_return)+'</dd></div></dl><p>'+esc(row.diagnosis_cn||'等待归因')+'</p></button>';}).join('')+'</div>';
  }
  function fmBindCandidateCards(){document.querySelectorAll('[data-fm-factor]').forEach(function(button){button.onclick=async function(){S.factor.selectedIndex=Number(button.dataset.fmFactor||0);fmInvalidateFactorViews();await render(true);};});}
  function fmRecentHistory(rows,limit){
    rows=arr(rows).slice(0,limit||6);if(!rows.length)return '<div class="fm-empty">暂无历史任务。</div>';
    return '<div class="fm-history-grid">'+rows.map(function(row){return '<article class="fm-history-card"><div><span class="fm-badge '+fmTone(row.status)+'">'+esc(fmStatus(row.status))+'</span></div><h4>'+esc(row.created_at||'--')+' · '+esc(valueText(row.universe||'ALL_A'))+'</h4><dl><div><dt>候选</dt><dd>'+fmCount(row.candidate_count)+'</dd></div><div><dt>通过</dt><dd>'+fmCount(row.accepted_count)+'</dd></div><div><dt>月份</dt><dd>'+fmCount(row.months)+'</dd></div><div><dt>耗时</dt><dd>'+fmDuration(row.elapsed_seconds)+'</dd></div></dl><button class="ghost-button" type="button" data-fm-load="'+esc(row.job_id)+'">载入结果</button></article>';}).join('')+'</div>';
  }
  function fmBindHistoryLoad(target){document.querySelectorAll('[data-fm-load]').forEach(function(button){button.onclick=async function(){this.disabled=true;try{await factorDetail(button.dataset.fmLoad,true);fmInvalidateFactorViews();S.active=target||'factor:expression';document.querySelectorAll('.nav-item').forEach(function(item){item.classList.toggle('is-active',item.dataset.target===S.active);});window.scrollTo({top:0,left:0,behavior:'auto'});await render(true);}finally{this.disabled=false;}};});}

  async function factorHome(){
    await needFactor(false);await factorDetail();
    const status=fmObj(S.factor.status),history=fRows(),factors=reports(),job=fmObj(S.factor.job&&['queued','running','failed'].includes(String(S.factor.job.status))?S.factor.job:S.factor.detail);
    clearConclusion();
    const controls='<section class="fm-controls"><div class="fm-control-grid"><label>股票池<select id="fu"><option value="ALL_A">全A</option><option value="CSI800_ENH">中证800增强域</option><option value="CSI2000_ENH">中证2000增强域</option></select></label><label>样本窗口<select id="fm"><option value="full">全窗口</option><option value="180">近180个月</option><option value="36">近36个月</option></select></label><label>目标通过数<input id="ft" type="number" min="1" max="20" value="1"></label><label>候选上限<input id="fmax" type="number" min="2" max="20" value="20"></label><label>迭代轮数<input id="fi" type="number" min="1" max="8" value="6"></label><label>每通道候选<input id="fb" type="number" min="1" max="8" value="6"></label><button id="fstart" class="action-button" type="button">开始挖掘</button></div></section>';
    const meta='<div class="fm-kpis"><article><span>GPT</span><strong>'+esc(status.model||'--')+'</strong><small>'+esc(status.reasoning_effort||'--')+'</small></article><article><span>模型引擎</span><strong>'+esc(status.model_engine_version||'--')+'</strong><small>严格GPT模式 '+(status.require_gpt?'开启':'关闭')+'</small></article><article><span>历史任务</span><strong>'+fmCount(history.length)+'</strong><small>账号与服务器去重</small></article><article><span>当前候选</span><strong>'+fmCount(factors.length)+'</strong><small>通过 '+fmCount(factors.filter(function(x){return x.accepted;}).length)+'</small></article></div>';
    root('<div class="fm-page">'+meta+controls+'<section class="fm-section"><div class="fm-section-head"><span>01</span><div><h2>模型进度</h2></div></div><div id="fm-job-panel">'+fmJobHTML(job)+'</div>'+fmFlowHTML(job)+'</section><section class="fm-section"><div class="fm-section-head"><span>02</span><div><h2>当前候选因子</h2></div></div>'+fmCandidateCards(factors,8)+'</section><section class="fm-section"><div class="fm-section-head"><span>03</span><div><h2>最近历史记录</h2></div></div>'+fmRecentHistory(history,6)+'</section></div>');
    fmBindCandidateCards();fmBindHistoryLoad('factor:expression');
    if($('fstart'))$('fstart').onclick=fStart;
  }
  async function fStart(){
    const button=$('fstart');if(button)button.disabled=true;
    try{
      const payload={universe:$('fu').value,max_months:$('fm').value,iterations:Number($('fi').value),target_accepted:Number($('ft').value),budget_per_channel:Number($('fb').value),max_candidates:Number($('fmax').value)};
      const job=await api('/api/factor/job/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      S.factor.job=job;S.factor.selectedJob=job.id||job.job_id;fmInvalidateFactorViews();await render(true);await fPoll(job.id||job.job_id);
    }catch(error){conclusion('因子任务提交失败：'+esc(error.message));if(button)button.disabled=false;}
  }
  async function fPoll(id){
    for(let count=0;count<2160&&id;count++){
      const job=await api('/api/factor/job/'+encodeURIComponent(id));S.factor.job=job;
      const panel=$('fm-job-panel');if(panel)panel.innerHTML=fmJobHTML(job);
      const flow=$('fm-flow');if(flow)flow.outerHTML=fmFlowHTML(job);
      if(!['queued','running'].includes(String(job.status))){
        if(job.status==='done'){S.factor.detail={result:job.result,job_id:id,status:'done',elapsed_seconds:job.elapsed_seconds};S.factor.selectedJob=id;S.factor.selectedIndex=0;await needFactor(true);fmInvalidateFactorViews();await render(true);}return;
      }
      await sleep(5000);
    }
  }
  function fmFeatureName(name){
    const map=factorVarLogic();return map[name]?map[name][0]:String(name||'').replace(/_/g,' ');
  }
  function fmDslMath(node){
    node=fmObj(node);const op=String(node.op||''),children=arr(node.children),windowValue=node.window;const child=node.child&&typeof node.child==='object'?fmDslMath(node.child):'';
    if(op==='feature')return '<mi>'+esc(fmFeatureName(node.name))+'</mi>';
    if(op==='neg')return '<mrow><mo>−</mo>'+child+'</mrow>';
    if(op==='rank'||op==='industry_rank'){const label=op==='industry_rank'?'行业内秩':'秩';return '<mrow><msub><mi>R</mi><mtext>'+label+'</mtext></msub><mo>(</mo>'+child+'<mo>)</mo></mrow>';}
    if(['ts_mean','ts_delta','ts_std','ts_zscore'].includes(op)){const names={ts_mean:'均值',ts_delta:'差分',ts_std:'波动',ts_zscore:'标准分'};return '<mrow><msub><mi>'+names[op]+'</mi><mn>'+esc(windowValue||'')+'</mn></msub><mo>(</mo>'+child+'<mo>)</mo></mrow>';}
    if(op==='graph_concept_residual')return '<mrow><msub><mi>残差</mi><mtext>隐概念</mtext></msub><mo>(</mo>'+child+'<mo>)</mo></mrow>';
    if(op==='add')return '<mrow>'+children.map(function(item,index){const coefficient=arr(node.weights)[index];return (index?'<mo>+</mo>':'')+(fmFinite(coefficient)!==null?'<mn>'+fmNum(coefficient)+'</mn><mo>·</mo>':'')+fmDslMath(item);}).join('')+'</mrow>';
    if(op==='multiply'||op==='mul')return '<mrow>'+children.map(function(item,index){return (index?'<mo>×</mo>':'')+fmDslMath(item);}).join('')+'</mrow>';
    if(op==='divide'||op==='ratio')return '<mfrac>'+(fmDslMath(node.left||children[0]))+(fmDslMath(node.right||children[1]))+'</mfrac>';
    if(child)return '<mrow><mi>'+esc(op||'算子')+'</mi><mo>(</mo>'+child+'<mo>)</mo></mrow>';
    return '<mtext>'+esc(op||'未定义')+'</mtext>';
  }
  function fmFormulaHTML(x){
    const dsl=fmObj(x.dsl),latex=String(x.latex_formula||x.formula||'');
    const visual=Object.keys(dsl).length?'<math display="block" aria-label="因子公式">'+fmDslMath(dsl)+'</math>':'<code>'+esc(latex||'暂无公式')+'</code>';
    return '<div class="fm-formula">'+visual+'</div><details class="fm-details"><summary>查看 LaTeX 与可执行 DSL</summary><pre>'+esc(latex)+'</pre><pre>'+esc(JSON.stringify(dsl,null,2))+'</pre></details>';
  }
  function fmFieldRows(x){
    const logic=factorVarLogic();return arr(x.data_fields).map(function(field){const name=fmFeatureName(field),meta=logic[field]||[name,'可执行字段','经济含义由因子构造给出'];return {proxy:name,name:name,formula:meta[1],logic:meta[2]};});
  }
  function fmDataScope(x){
    const names=arr(x.data_fields).map(fmFeatureName).filter(Boolean);return names.length?names.join(' × '):'--';
  }
  function fmSimpleTable(title,rows,columns){
    rows=arr(rows);return '<div class="fm-table"><div class="fm-table-title"><h3>'+esc(title)+'</h3></div><div class="fm-table-scroll"><table><thead><tr>'+columns.map(function(c){return '<th>'+esc(c.label)+'</th>';}).join('')+'</tr></thead><tbody>'+rows.map(function(row){return '<tr>'+columns.map(function(c){const value=typeof c.value==='function'?c.value(row):row[c.key];return '<td>'+esc(value==null||value===''?'--':value)+'</td>';}).join('')+'</tr>';}).join('')+'</tbody></table></div></div>';
  }
  function factorExpression(){
    const x=selectedFactor(),fields=fmFieldRows(x);
    conclusion('当前因子 '+esc(x.chinese_name||x.name||'--')+'，状态 '+esc(fmStatus(x.status||x.lifecycle_state))+'，部署置信度 '+fmPct(x.lifecycle_deployment_confidence)+'。');
    root('<div class="fm-page">'+fmContextHTML()+'<section class="fm-section"><div class="fm-section-head"><span>01</span><div><h2>'+esc(x.chinese_name||x.name||x.factor||'暂无因子')+'</h2></div></div><div class="fm-meta-row"><span class="fm-badge '+fmTone(x.status)+'">'+esc(fmStatus(x.status))+'</span><span>复杂度：'+fmNum(x.complexity)+'</span><span>部署置信度：'+fmPct(x.lifecycle_deployment_confidence)+'</span><span>数据域：'+esc(fmDataScope(x))+'</span></div>'+fmFormulaHTML(x)+'</section><section class="fm-section"><div class="fm-section-head"><span>02</span><div><h2>经济假设与构造链</h2></div></div><div class="fm-narrative"><h3>经济假设</h3><p>'+esc(x.hypothesis||'未提供')+'</p><h3>程序构造</h3><p>'+esc(x.construction||'未提供')+'</p></div>'+fmSimpleTable('中文变量与构造',fields,[{key:'proxy',label:'代理变量'},{key:'name',label:'变量名称'},{key:'formula',label:'计算公式'},{key:'logic',label:'经济逻辑'}])+'</section><section class="fm-section"><div class="fm-section-head"><span>03</span><div><h2>候选因子池</h2></div></div>'+fmCandidateCards(reports(),20)+'</section></div>');
    fmBindContext();fmBindCandidateCards();
  }
  function fmMetricCards(items){
    return '<div class="fm-metric-grid">'+arr(items).map(function(item){return '<article class="fm-metric '+(item.tone||'')+'"><span>'+esc(item.label)+'</span><strong>'+esc(item.value)+'</strong>'+(item.note?'<small>'+esc(item.note)+'</small>':'')+'</article>';}).join('')+'</div>';
  }
  function fmGateCards(items){
    return '<div class="fm-gate-grid">'+arr(items).map(function(item){const tone=item.state||'watch';return '<article class="fm-gate '+tone+'"><span>'+esc(item.label)+'</span><strong>'+(tone==='pass'?'通过':tone==='fail'?'未通过':'观察')+'</strong><small>'+esc(item.note||'')+'</small></article>';}).join('')+'</div>';
  }
  function fmChartBox(id,title,subtitle,wide){
    return '<section class="fm-chart '+(wide?'wide':'')+'"><header><div><h3>'+esc(title)+'</h3></div></header><div id="'+id+'" class="fm-plot"></div></section>';
  }
  function fmSection(index,id,title,subtitle,body){
    return '<section class="fm-section" id="'+id+'"><div class="fm-section-head"><span>'+String(index).padStart(2,'0')+'</span><div><h2>'+esc(title)+'</h2></div></div>'+body+'</section>';
  }
  function fmJumpNav(){
    const items=[['fm-overview','综合总览'],['fm-splits','分段检验'],['fm-ic','IC时序'],['fm-groups','五组收益'],['fm-backtest','净值回撤'],['fm-annual','年度衰减'],['fm-walk','滚动外样本'],['fm-purged','隔离K折'],['fm-regime','状态稳健'],['fm-incremental','独立增量'],['fm-overfit','过拟合审计'],['fm-attribution','分层归因'],['fm-audit','数据与因果审计']];
    return '<nav class="fm-jump" aria-label="因子检验目录">'+items.map(function(item){return '<a href="#'+item[0]+'">'+item[1]+'</a>';}).join('')+'</nav>';
  }
  function fmSplitMetric(x,name){
    const metrics=fmObj(x.metrics),split=fmObj(metrics[name]),backtest=fmObj(split.backtest);
    const longOnly=fmFirstObj(backtest.long_only,split.long_only),longShort=fmFirstObj(backtest.long_short,split.long_short);
    const rankFallback=name==='train'?x.train_rank_ic:name==='valid'?x.valid_rank_ic:name==='test'?x.test_rank_ic:null;
    return {split:name,rank_ic:fmFinite(split.rank_ic,rankFallback),group_spread:fmFinite(split.group_spread),coverage:fmFinite(split.coverage),long_return:fmFinite(longOnly.annual_return),long_excess:fmFinite(longOnly.excess_annual_return),information_ratio:fmFinite(longOnly.information_ratio),long_short_return:fmFinite(longShort.annual_return),long_short_sharpe:fmFinite(longShort.sharpe),long_short_drawdown:fmFinite(longShort.max_drawdown),turnover:fmFinite(backtest.avg_turnover,split.turnover)};
  }
  function fmSplitRows(x){return ['train','valid','test','full'].map(function(name){return fmSplitMetric(x,name);});}
  function fmSplitName(value){return {train:'训练期',valid:'验证期',test:'测试期',full:'全窗口'}[value]||value;}
  function fmCurve(x){
    const metrics=fmObj(x.metrics),test=fmObj(metrics.test),full=fmObj(metrics.full);
    return fmFirstArray(x.backtest_curve,fmObj(test.backtest).curve,test.backtest_curve,fmObj(full.backtest).curve,full.backtest_curve).map(function(row){return Object.assign({},row,{date:fmDate(row.date)});});
  }
  function fmICRows(x){return arr(x.ic_series).map(function(row){return {date:fmDate(row.date||row.year),rank_ic:fmFinite(row.rank_ic,row.ic),group_spread:fmFinite(row.group_spread),coverage:fmFinite(row.coverage),turnover:fmFinite(row.turnover)};}).filter(function(row){return row.date;});}
  function fmRolling(rows,key,windowSize){
    return rows.map(function(_,index){const start=Math.max(0,index-windowSize+1),values=rows.slice(start,index+1).map(function(row){return fmFinite(row[key]);}).filter(function(value){return value!==null;});return values.length?values.reduce(function(a,b){return a+b;},0)/values.length:null;});
  }
  function fmAnnualRows(x){return arr(x.annual_summary).map(function(row){return {year:String(row.year||String(row.date||'').slice(0,4)),rank_ic:fmFinite(row.rank_ic,row.ic),group_spread:fmFinite(row.group_spread,row.spread),long_return:fmFinite(row.long_return),benchmark_return:fmFinite(row.benchmark_return),long_short_return:fmFinite(row.long_short_return),positive_ic_rate:fmFinite(row.positive_ic_rate),coverage:fmFinite(row.coverage)};}).filter(function(row){return row.year;});}
  function fmWalkRows(x){const walk=fmObj(x.walk_forward);return arr(walk.windows||walk.folds).map(function(row,index){return {window:index+1,period:row.test_year||row.test_period||row.test||((row.test_start||'')+'至'+(row.test_end||'')),train_rank_ic:fmFinite(row.train_rank_ic,row.train_ic),test_rank_ic:fmFinite(row.test_rank_ic,row.test_ic),decay:fmFinite(row.decay),group_spread:fmFinite(row.test_group_spread),coverage:fmFinite(row.test_coverage)};});}
  function fmPurgedRows(x){const purged=fmObj(x.purged_kfold);return arr(purged.folds).map(function(row,index){return {fold:row.fold||index+1,period:fmDateText(row.test_start)+' 至 '+fmDateText(row.test_end),train_rank_ic:fmFinite(row.train_rank_ic,row.train_ic),test_rank_ic:fmFinite(row.test_rank_ic,row.test_ic),decay:fmFinite(row.decay),group_spread:fmFinite(row.test_group_spread),coverage:fmFinite(row.test_coverage),purge_periods:row.purge_periods,test_periods:row.test_periods};});}
  function fmRegimeRows(x){
    const regime=fmObj(x.regime_evidence),rows=[];
    ['valid','test'].forEach(function(split){arr(fmObj(regime[split]).states).forEach(function(row){rows.push({split:split,state:fmState(row.state),observations:row.observations,posterior_mean:fmFinite(row.posterior_mean),lower_90:fmFinite(row.lower_90),positive_probability:fmFinite(row.positive_probability),sample_mean:fmFinite(row.sample_mean)});});});
    return rows;
  }
  function fmIncrementalRows(x){
    const incremental=fmObj(x.incremental_evidence);return ['valid','test'].map(function(split){const row=fmObj(incremental[split]);return {split:split,baseline_rank_ic:fmFinite(row.baseline_rank_ic),combined_rank_ic:fmFinite(row.combined_rank_ic),residual_rank_ic:fmFinite(row.residual_rank_ic),residual_probability:fmFinite(fmObj(row.residual_posterior).positive_probability),marginal_gain:fmFinite(row.marginal_rank_ic_gain),marginal_probability:fmFinite(fmObj(row.marginal_posterior).positive_probability),periods:fmFinite(row.residual_periods)};});
  }
  function fmAttributionRows(x){
    const attribution=fmFirstObj(fmObj(x.metrics).attribution,x.attribution),rows=[];
    [['industry','行业'],['size','市值层'],['liquidity','流动性层']].forEach(function(spec){arr(attribution[spec[0]]).forEach(function(row){rows.push({dimension:spec[1],bucket:String(row.bucket||'').toUpperCase()==='UNCLASSIFIED'?'未分类':cnText(row.bucket),rank_ic:fmFinite(row.rank_ic),rows:row.rows});});});
    return rows;
  }
  function fmCoreRows(x){
    const incremental=fmObj(x.incremental_evidence),validInc=fmObj(incremental.valid),testInc=fmObj(incremental.test),regime=fmObj(x.regime_evidence),validRegime=fmObj(regime.valid),search=fmObj(x.posterior_search_evidence),final=fmObj(x.posterior_final_evidence);
    return [
      {metric:'最终状态',value:fmStatus(x.status),meaning:x.diagnosis_cn||''},
      {metric:'训练RankIC',value:fmNum(x.train_rank_ic),meaning:'训练集仅用于方向与参数学习。'},
      {metric:'验证RankIC',value:fmNum(x.valid_rank_ic),meaning:'验证集用于选择、裁判和变异反馈。'},
      {metric:'测试RankIC',value:fmNum(x.test_rank_ic),meaning:'封存测试集仅做最终报告。'},
      {metric:'验证残差RankIC',value:fmNum(fmFinite(validInc.residual_rank_ic,x.valid_incremental_residual_rank_ic)),meaning:'剔除训练期冻结基线后仍保留的信息。'},
      {metric:'测试残差RankIC',value:fmNum(fmFinite(testInc.residual_rank_ic,x.test_incremental_residual_rank_ic)),meaning:'封存期增量信息，只用于审计。'},
      {metric:'验证组合边际',value:fmNum(fmFinite(validInc.marginal_rank_ic_gain,x.valid_downstream_marginal_rank_ic_gain)),meaning:'加入冻结组合后的净RankIC增量。'},
      {metric:'状态正向广度',value:fmPct(fmFinite(validRegime.posterior_positive_breadth,x.valid_regime_positive_breadth)),meaning:'训练期定义状态中的后验正向覆盖。'},
      {metric:'训练验证联合后验',value:fmPct(fmFinite(search.joint_positive_probability,x.posterior_joint_positive_probability)),meaning:'不含测试集的研究裁判概率。'},
      {metric:'最终联合后验',value:fmPct(fmFinite(final.joint_positive_probability,x.posterior_final_joint_positive_probability)),meaning:'加入封存测试后，仅作报告。'},
      {metric:'多空年化收益',value:fmPct(x.test_long_short_annual_return),meaning:'Top减Bottom并扣成本。'},
      {metric:'多空夏普',value:fmNum(x.test_long_short_sharpe),meaning:'测试期风险调整收益。'},
      {metric:'多空最大回撤',value:fmPct(x.test_long_short_max_drawdown),meaning:'测试期峰谷回撤。'},
      {metric:'DSR置信度',value:fmPct(x.test_long_short_deflated_sharpe_confidence),meaning:'按有效独立试验数修正后的夏普置信度。'},
      {metric:'PBO代理',value:fmPct(x.pbo_proxy),meaning:'隔离折排名迁移推断的过拟合风险。'},
      {metric:'部署置信度',value:fmPct(x.lifecycle_deployment_confidence),meaning:'生命周期和近期兑现的连续缩放建议。'}
    ];
  }
  function fmGates(x){
    const staticAudit=fmObj(x.static_audit),lifecycle=String(x.lifecycle_state||'');
    return [
      {label:'快速初筛',state:x.quick_passed?'pass':'fail',note:'覆盖、极值、粗排IC与稳定性'},
      {label:'静态因果审计',state:staticAudit.passed===false?'fail':'pass',note:'字段、时序算子与标签泄漏'},
      {label:'信号质量',state:x.signal_pass?'pass':'fail',note:'训练、验证与分组单调性'},
      {label:'搜索可靠性',state:x.search_reliability_pass?'pass':'fail',note:'只使用训练与验证证据'},
      {label:'后验研究证据',state:x.posterior_search_pass?'pass':'fail',note:'多证据联合后验'},
      {label:'独立增量',state:fmFinite(x.valid_incremental_residual_rank_ic)>0?'pass':'fail',note:'残差信息与组合边际'},
      {label:'新颖性约束',state:x.novelty_pass===false?'fail':'pass',note:'排除声明父代后的相关性'},
      {label:'市场中性',state:x.market_neutral_pass?'pass':'fail',note:'多空收益与回撤'},
      {label:'多头增强',state:x.long_only_pass?'pass':'fail',note:'相对基准超额收益'},
      {label:'生命周期',state:lifecycle==='healthy'?'pass':'watch',note:fmStatus(lifecycle)},
      {label:'生产就绪',state:x.lifecycle_production_ready?'pass':'watch',note:x.lifecycle_production_ready_reason||'仍需影子期确认'}
    ];
  }
  function fmDrawSplit(id,rows){
    const names=rows.map(function(row){return fmSplitName(row.split);});
    plot(id,[{type:'bar',name:'RankIC',x:names,y:rows.map(function(row){return row.rank_ic;}),marker:{color:'#2f5f9f'}},{type:'scatter',mode:'lines+markers',name:'分组收益差',x:names,y:rows.map(function(row){return row.group_spread;}),yaxis:'y2',line:{color:'#276b58',width:2.4}}],{height:320,barmode:'group',yaxis:{title:'RankIC'},yaxis2:{title:'收益差',overlaying:'y',side:'right',showgrid:false},xaxis:{showgrid:false},legend:{orientation:'h',y:-.23}});
  }
  function fmDrawIC(id,rows){
    const x=rows.map(function(row){return row.date;}),rolling=fmRolling(rows,'rank_ic',6);
    plot(id,[{type:'bar',name:'月度RankIC',x:x,y:rows.map(function(row){return row.rank_ic;}),marker:{color:rows.map(function(row){return Number(row.rank_ic)>=0?'#276b58':'#9d3b3b';})}},{type:'scatter',mode:'lines',name:'6期滚动均值',x:x,y:rolling,line:{color:'#2f5f9f',width:2.4}}],{height:340,hovermode:'x unified',yaxis:{title:'RankIC',zeroline:true},xaxis:{type:'date',tickformat:'%Y-%m',rangeslider:{visible:true,thickness:.08}},legend:{orientation:'h',y:-.30}});
  }
  function fmDrawGroup(id,rows){
    plot(id,[{type:'bar',x:rows.map(function(row){return '第'+row.group+'组';}),y:rows.map(function(row){return row.return*100;}),text:rows.map(function(row){return fmPct(row.return);}),textposition:'outside',marker:{color:['#dcece5','#a8cfbd','#72ad91','#477d6b','#245d4d']}}],{height:310,showlegend:false,yaxis:{title:'平均月收益（%）',ticksuffix:'%'},xaxis:{showgrid:false}});
  }
  function fmDrawNav(id,curve){
    plot(id,[{type:'scatter',mode:'lines',name:'多头净值',x:curve.map(function(row){return row.date;}),y:curve.map(function(row){return row.long_nav;}),line:{color:'#2f5f9f',width:2.4}},{type:'scatter',mode:'lines',name:'基准净值',x:curve.map(function(row){return row.date;}),y:curve.map(function(row){return row.benchmark_nav;}),line:{color:'#777',width:1.8}},{type:'scatter',mode:'lines',name:'多空净值',x:curve.map(function(row){return row.date;}),y:curve.map(function(row){return row.long_short_nav;}),line:{color:'#276b58',width:2.4}}],{height:360,hovermode:'x unified',yaxis:{title:'净值'},xaxis:{type:'date',tickformat:'%Y-%m',rangeslider:{visible:true,thickness:.08}},legend:{orientation:'h',y:-.30}});
  }
  function fmDrawDrawdown(id,curve){
    plot(id,[{type:'scatter',mode:'lines',name:'多头回撤',x:curve.map(function(row){return row.date;}),y:curve.map(function(row){return Number(row.long_drawdown)*100;}),line:{color:'#2f5f9f',width:2}},{type:'scatter',mode:'lines',name:'基准回撤',x:curve.map(function(row){return row.date;}),y:curve.map(function(row){return Number(row.benchmark_drawdown)*100;}),line:{color:'#777',width:1.6}},{type:'scatter',mode:'lines',name:'多空回撤',x:curve.map(function(row){return row.date;}),y:curve.map(function(row){return Number(row.long_short_drawdown)*100;}),line:{color:'#9d3b3b',width:2}}],{height:360,hovermode:'x unified',yaxis:{title:'回撤（%）',ticksuffix:'%'},xaxis:{type:'date',tickformat:'%Y-%m'},legend:{orientation:'h',y:-.28}});
  }
  function fmDrawAnnual(id,rows){
    const specs=[['rank_ic','RankIC',.08],['group_spread','分组收益差',.03],['long_short_return','多空收益',.15],['positive_ic_rate','正IC比例',.5],['coverage','覆盖率',.1]];
    const z=rows.map(function(row){return specs.map(function(spec){const value=fmFinite(row[spec[0]]);if(value===null)return 0;if(spec[0]==='positive_ic_rate')return fmClamp((value-.5)/spec[2],-1,1);if(spec[0]==='coverage')return fmClamp((value-.9)/spec[2],-1,1);return fmClamp(value/spec[2],-1,1);});});
    const text=rows.map(function(row){return specs.map(function(spec){return ['positive_ic_rate','coverage','long_short_return'].includes(spec[0])?fmPct(row[spec[0]]):fmNum(row[spec[0]]);});});
    plot(id,[{type:'heatmap',x:specs.map(function(spec){return spec[1];}),y:rows.map(function(row){return row.year;}),z:z,text:text,texttemplate:'%{text}',zmin:-1,zmax:1,colorscale:[[0,'#9d3b3b'],[.5,'#f7f8fa'],[1,'#245d4d']],showscale:false,hovertemplate:'%{y}<br>%{x} %{text}<extra></extra>'}],{height:Math.max(260,120+rows.length*42),xaxis:{showgrid:false},yaxis:{showgrid:false,autorange:'reversed'},margin:{l:62,r:18,t:16,b:46}});
  }
  function fmDrawWalk(id,rows){
    plot(id,[{type:'scatter',mode:'lines+markers',name:'训练RankIC',x:rows.map(function(row){return String(row.period);}),y:rows.map(function(row){return row.train_rank_ic;}),line:{color:'#667085',width:2}},{type:'scatter',mode:'lines+markers',name:'样本外RankIC',x:rows.map(function(row){return String(row.period);}),y:rows.map(function(row){return row.test_rank_ic;}),line:{color:'#2f5f9f',width:2.5}},{type:'bar',name:'衰减',x:rows.map(function(row){return String(row.period);}),y:rows.map(function(row){return row.decay;}),marker:{color:'rgba(183,121,31,.45)'}}],{height:330,barmode:'overlay',yaxis:{title:'RankIC / 衰减'},xaxis:{showgrid:false},legend:{orientation:'h',y:-.28}});
  }
  function fmDrawPurged(id,rows){
    plot(id,[{type:'bar',name:'隔离折测试RankIC',x:rows.map(function(row){return '第'+row.fold+'折';}),y:rows.map(function(row){return row.test_rank_ic;}),marker:{color:rows.map(function(row){return Number(row.test_rank_ic)>=0?'#276b58':'#9d3b3b';})}},{type:'scatter',mode:'lines+markers',name:'训练RankIC',x:rows.map(function(row){return '第'+row.fold+'折';}),y:rows.map(function(row){return row.train_rank_ic;}),line:{color:'#2f5f9f',width:2}}],{height:310,yaxis:{title:'RankIC'},xaxis:{showgrid:false},legend:{orientation:'h',y:-.25}});
  }
  function fmDrawRegime(id,rows){
    const valid=rows.filter(function(row){return row.split==='valid';}),test=rows.filter(function(row){return row.split==='test';}),states=Array.from(new Set(rows.map(function(row){return row.state;})));
    function lookup(source,state,key){const row=source.find(function(item){return item.state===state;});return row?row[key]:null;}
    plot(id,[{type:'bar',name:'验证90%下界',x:states,y:states.map(function(state){return lookup(valid,state,'lower_90');}),marker:{color:'#276b58'}},{type:'bar',name:'测试90%下界',x:states,y:states.map(function(state){return lookup(test,state,'lower_90');}),marker:{color:'#2f5f9f'}},{type:'scatter',mode:'lines+markers',name:'验证正向概率',x:states,y:states.map(function(state){return Number(lookup(valid,state,'positive_probability'))*100;}),yaxis:'y2',line:{color:'#b7791f',width:2}}],{height:330,barmode:'group',yaxis:{title:'RankIC后验下界'},yaxis2:{title:'正向概率（%）',overlaying:'y',side:'right',range:[0,105],showgrid:false,ticksuffix:'%'},xaxis:{showgrid:false},legend:{orientation:'h',y:-.26}});
  }
  function fmDrawIncremental(id,rows){
    const names=rows.map(function(row){return fmSplitName(row.split);});
    plot(id,[{type:'bar',name:'残差RankIC',x:names,y:rows.map(function(row){return row.residual_rank_ic;}),marker:{color:'#2f5f9f'}},{type:'bar',name:'组合边际RankIC',x:names,y:rows.map(function(row){return row.marginal_gain;}),marker:{color:'#276b58'}},{type:'scatter',mode:'lines+markers',name:'联合后RankIC',x:names,y:rows.map(function(row){return row.combined_rank_ic;}),line:{color:'#b7791f',width:2.2}}],{height:320,barmode:'group',yaxis:{title:'RankIC'},xaxis:{showgrid:false},legend:{orientation:'h',y:-.26}});
  }
  function fmOverfitRows(x){
    const search=fmObj(x.posterior_search_evidence),final=fmObj(x.posterior_final_evidence),rows=[];
    function add(metric,value,kind,note){if(fmFinite(value)!==null)rows.push({metric:metric,value:Number(value),kind:kind,note:note});}
    add('DSR置信度',x.test_long_short_deflated_sharpe_confidence,'confidence','多重试验修正后的夏普置信度');
    add('隔离K折正IC比例',x.purged_positive_ratio,'confidence','相邻期隔离后信号存活率');
    if(x.pbo_proxy!==null&&x.pbo_proxy!==undefined)add('PBO安全度',1-Number(x.pbo_proxy),'confidence','一减去过拟合概率代理');
    if(x.search_cscv_candidate_evidence_available)add('搜索CSCV安全度',1-Number(x.search_cscv_probability_overfit_above_half),'confidence','训练验证互补分块后验安全度');
    add('训练验证联合后验',fmFinite(search.joint_positive_probability,x.posterior_joint_positive_probability),'confidence','不含测试集');
    add('最终联合后验',fmFinite(final.joint_positive_probability,x.posterior_final_joint_positive_probability),'confidence','测试仅作最终报告');
    add('新颖性保留度',1-Number(x.redundancy_max_abs_corr||0),'confidence','一减去父代外最大相关');
    return rows;
  }
  function fmDrawOverfit(id,rows){plot(id,[{type:'bar',orientation:'h',x:rows.map(function(row){return row.value*100;}),y:rows.map(function(row){return row.metric;}),text:rows.map(function(row){return fmPct(row.value);}),textposition:'auto',marker:{color:rows.map(function(row){return row.value>=.7?'#276b58':row.value>=.5?'#b7791f':'#9d3b3b';})}}],{height:Math.max(300,120+rows.length*38),showlegend:false,xaxis:{title:'置信/安全度（%）',range:[0,100],ticksuffix:'%'},yaxis:{automargin:true},margin:{l:150,r:24,t:16,b:50}});}
  function fmDrawAttribution(id,rows){
    const industry=rows.filter(function(row){return row.dimension==='行业';}).slice(0,10).reverse();
    plot(id,[{type:'bar',orientation:'h',x:industry.map(function(row){return row.rank_ic;}),y:industry.map(function(row){return row.bucket;}),text:industry.map(function(row){return fmNum(row.rank_ic);}),textposition:'auto',marker:{color:'#276b58'}}],{height:380,showlegend:false,xaxis:{title:'RankIC'},yaxis:{automargin:true},margin:{l:95,r:22,t:16,b:48}});
  }
  function fmAuditRows(x){
    const result=fResult(),split=fmObj(result.split_audit),execution=fmObj(result.execution_audit),staticAudit=fmObj(x.static_audit),temporal=fmObj(staticAudit.causal_temporal_audit),implementation=fmObj(x.implementation_audit),rows=[];
    ['train','valid','test','full'].forEach(function(name){const row=fmObj(split[name]);if(Object.keys(row).length)rows.push({check:fmSplitName(name)+'样本边界',value:fmDateText(row.start)+' 至 '+fmDateText(row.end),status:'通过',detail:fmCount(row.months)+'期 / '+fmCount(row.rows)+'行 / 标签覆盖 '+fmPct(row.label_coverage)});});
    rows.push({check:'执行时点',value:'信号收盘后首个可交易开盘',status:execution.one_price_limit_and_suspension_entry_guard?'通过':'检查',detail:'平均延迟 '+fmSig(execution.execution_delay_days_mean,1,'天')+'；涨跌停与停牌约束'});
    rows.push({check:'未来信息',value:temporal.past_only===false?'发现风险':'仅当前及过去观测',status:temporal.past_only===false?'未通过':'通过',detail:'窗口模式 '+fmStatus(temporal.mode||'未使用时序算子')});
    rows.push({check:'标签隔离',value:'训练/验证边界隔离',status:'通过',detail:'训练与验证末端均保留标签禁区'});
    rows.push({check:'目标拟合',value:implementation.target_fitted?'仅训练期拟合':'无目标拟合',status:'通过',detail:'验证与测试映射冻结'});
    rows.push({check:'静态审计',value:staticAudit.passed===false?'存在阻断项':'无阻断项',status:staticAudit.passed===false?'未通过':'通过',detail:arr(staticAudit.issues).length+' 个警告/问题'});
    return rows;
  }
  function fmDrawAudit(id,rows){
    const splitRows=fmSplitRows(selectedFactor());
    plot(id,[{type:'bar',name:'覆盖率',x:splitRows.map(function(row){return fmSplitName(row.split);}),y:splitRows.map(function(row){return Number(row.coverage)*100;}),marker:{color:'#276b58'},text:splitRows.map(function(row){return fmPct(row.coverage);}),textposition:'auto'}],{height:280,showlegend:false,yaxis:{title:'覆盖率（%）',range:[0,105],ticksuffix:'%'},xaxis:{showgrid:false}});
  }
  function fmDrawReport(x,ids,data){
    fmDrawSplit(ids.split,data.splits);fmDrawIC(ids.ic,data.ic);fmDrawGroup(ids.group,data.groups);fmDrawNav(ids.nav,data.curve);fmDrawDrawdown(ids.dd,data.curve);fmDrawAnnual(ids.annual,data.annual);fmDrawWalk(ids.walk,data.walk);fmDrawPurged(ids.purged,data.purged);fmDrawRegime(ids.regime,data.regime);fmDrawIncremental(ids.incremental,data.incremental);fmDrawOverfit(ids.overfit,data.overfit);fmDrawAttribution(ids.attribution,data.attribution);fmDrawAudit(ids.audit,data.audit);
  }
  function factorReport(){
    const x=selectedFactor(),splits=fmSplitRows(x),ic=fmICRows(x),curve=fmCurve(x),annual=fmAnnualRows(x),walk=fmWalkRows(x),purged=fmPurgedRows(x),regime=fmRegimeRows(x),incremental=fmIncrementalRows(x),overfit=fmOverfitRows(x),attribution=fmAttributionRows(x),audit=fmAuditRows(x);
    const groupsRaw=fmFirstArray(x.group_returns,fmObj(fmObj(x.metrics).test).group_returns),groups=groupsRaw.map(function(value,index){return {group:index+1,return:Number(value)};});
    const ids={split:pid('fmsp'),ic:pid('fmic'),group:pid('fmgr'),nav:pid('fmnav'),dd:pid('fmdd'),annual:pid('fmyr'),walk:pid('fmwf'),purged:pid('fmpk'),regime:pid('fmrg'),incremental:pid('fminc'),overfit:pid('fmob'),attribution:pid('fmat'),audit:pid('fmad')};
    const core=fmCoreRows(x),quick=fmObj(x.quick_screen),posterior=fmObj(x.posterior_search_evidence),validRegime=fmObj(fmObj(x.regime_evidence).valid);
    conclusion('当前因子 '+esc(x.chinese_name||x.name||'--')+'：测试RankIC '+fmNum(x.test_rank_ic)+'，多空年化 '+fmPct(x.test_long_short_annual_return)+'，多空夏普 '+fmNum(x.test_long_short_sharpe)+'。');
    const summaryMetrics=fmMetricCards([{label:'测试RankIC',value:fmNum(x.test_rank_ic),note:'封存样本外'},{label:'验证残差RankIC',value:fmNum(x.valid_incremental_residual_rank_ic),note:'冻结基线后增量'},{label:'验证组合边际',value:fmNum(x.valid_downstream_marginal_rank_ic_gain),note:'加入组合净贡献'},{label:'多空年化',value:fmPct(x.test_long_short_annual_return),note:'成本后'},{label:'多空夏普',value:fmNum(x.test_long_short_sharpe),note:'测试期'},{label:'多空回撤',value:fmPct(x.test_long_short_max_drawdown),note:'测试期'},{label:'状态正向广度',value:fmPct(fmFinite(validRegime.posterior_positive_breadth,x.valid_regime_positive_breadth)),note:'验证期后验'},{label:'联合后验',value:fmPct(fmFinite(posterior.joint_positive_probability,x.posterior_joint_positive_probability)),note:'训练+验证'},{label:'DSR置信度',value:fmPct(x.test_long_short_deflated_sharpe_confidence),note:'多重试验修正'},{label:'部署置信度',value:fmPct(x.lifecycle_deployment_confidence),note:fmStatus(x.lifecycle_state)}]);
    const splitTable=fmSimpleTable('训练 / 验证 / 测试 / 全窗指标',splits,[{label:'集合',value:function(row){return fmSplitName(row.split);}},{label:'RankIC',value:function(row){return fmNum(row.rank_ic);}},{label:'分组差',value:function(row){return fmPct(row.group_spread);}},{label:'覆盖率',value:function(row){return fmPct(row.coverage);}},{label:'多头年化',value:function(row){return fmPct(row.long_return);}},{label:'多空年化',value:function(row){return fmPct(row.long_short_return);}},{label:'多空夏普',value:function(row){return fmNum(row.long_short_sharpe);}},{label:'多空回撤',value:function(row){return fmPct(row.long_short_drawdown);}}]);
    const icTable=fmSimpleTable('测试期月度IC明细',ic,[{label:'日期',value:function(row){return row.date;}},{label:'RankIC',value:function(row){return fmNum(row.rank_ic);}},{label:'分组收益差',value:function(row){return fmPct(row.group_spread);}},{label:'覆盖率',value:function(row){return fmPct(row.coverage);}},{label:'换手',value:function(row){return fmPct(row.turnover);}}]);
    const groupTable=fmSimpleTable('五组收益表',groups,[{label:'组别',value:function(row){return '第'+row.group+'组';}},{label:'平均月收益',value:function(row){return fmPct(row.return);}},{label:'含义',value:function(row){return row.group===1?'低因子组':row.group===groups.length?'高因子组':'中间分组';}}]);
    const annualTable=fmSimpleTable('年度稳定性',annual,[{key:'year',label:'年份'},{label:'RankIC',value:function(row){return fmNum(row.rank_ic);}},{label:'分组差',value:function(row){return fmPct(row.group_spread);}},{label:'多头收益',value:function(row){return fmPct(row.long_return);}},{label:'基准收益',value:function(row){return fmPct(row.benchmark_return);}},{label:'多空收益',value:function(row){return fmPct(row.long_short_return);}},{label:'正IC比例',value:function(row){return fmPct(row.positive_ic_rate);}},{label:'覆盖率',value:function(row){return fmPct(row.coverage);}}]);
    const walkTable=fmSimpleTable('滚动样本外窗口',walk,[{key:'window',label:'窗口'},{key:'period',label:'测试区间'},{label:'训练RankIC',value:function(row){return fmNum(row.train_rank_ic);}},{label:'样本外RankIC',value:function(row){return fmNum(row.test_rank_ic);}},{label:'衰减',value:function(row){return fmNum(row.decay);}},{label:'分组差',value:function(row){return fmPct(row.group_spread);}},{label:'覆盖率',value:function(row){return fmPct(row.coverage);}}]);
    const purgedTable=fmSimpleTable('隔离K折明细',purged,[{key:'fold',label:'折'},{key:'period',label:'测试区间'},{label:'训练RankIC',value:function(row){return fmNum(row.train_rank_ic);}},{label:'隔离折RankIC',value:function(row){return fmNum(row.test_rank_ic);}},{label:'衰减',value:function(row){return fmNum(row.decay);}},{label:'分组差',value:function(row){return fmPct(row.group_spread);}},{key:'purge_periods',label:'隔离期'}]);
    const regimeTable=fmSimpleTable('市场状态后验',regime,[{label:'集合',value:function(row){return fmSplitName(row.split);}},{key:'state',label:'状态'},{key:'observations',label:'观测数'},{label:'后验均值',value:function(row){return fmNum(row.posterior_mean);}},{label:'90%下界',value:function(row){return fmNum(row.lower_90);}},{label:'正向概率',value:function(row){return fmPct(row.positive_probability);}}]);
    const incrementalTable=fmSimpleTable('独立增量与组合协同',incremental,[{label:'集合',value:function(row){return fmSplitName(row.split);}},{label:'基线RankIC',value:function(row){return fmNum(row.baseline_rank_ic);}},{label:'组合RankIC',value:function(row){return fmNum(row.combined_rank_ic);}},{label:'残差RankIC',value:function(row){return fmNum(row.residual_rank_ic);}},{label:'残差正向概率',value:function(row){return fmPct(row.residual_probability);}},{label:'组合边际',value:function(row){return fmNum(row.marginal_gain);}},{label:'边际正向概率',value:function(row){return fmPct(row.marginal_probability);}}]);
    const overfitTable=fmSimpleTable('多重检验与过拟合风险',overfit,[{key:'metric',label:'证据'},{label:'数值',value:function(row){return fmPct(row.value);}},{key:'note',label:'口径'}]);
    const attributionTable=fmSimpleTable('行业 / 市值 / 流动性归因',attribution,[{key:'dimension',label:'维度'},{key:'bucket',label:'分层'},{label:'RankIC',value:function(row){return fmNum(row.rank_ic);}},{label:'样本数',value:function(row){return fmCount(row.rows);}}]);
    const auditTable=fmSimpleTable('数据与因果审计',audit,[{key:'check',label:'检查项'},{key:'value',label:'口径/边界'},{key:'status',label:'结果'},{key:'detail',label:'证据'}]);
    root('<div class="fm-page">'+fmContextHTML()+fmJumpNav()+fmSection(1,'fm-overview','综合检验总览','先看是否通过，再向下定位每个证据来源。',fmGateCards(fmGates(x))+summaryMetrics+fmSimpleTable('综合检验表',core,[{key:'metric',label:'指标'},{key:'value',label:'数值'},{key:'meaning',label:'解释'}]))+fmSection(2,'fm-splits','训练、验证、测试与全窗分段','统一口径比较RankIC、分组差、成本后收益和回撤。','<div class="fm-visual-grid">'+fmChartBox(ids.split,'分段信号与收益差','测试集不参与模型选择')+splitTable+'</div>')+fmSection(3,'fm-ic','RankIC时序与稳定性','月度横截面秩相关、六期滚动均值及覆盖率。',fmChartBox(ids.ic,'RankIC时序','绿色为正、红色为负；横轴已转换为真实交易日期',true)+icTable)+fmSection(4,'fm-groups','五组分组收益与单调性','从低因子组到高因子组检查收益是否有序。','<div class="fm-visual-grid">'+fmChartBox(ids.group,'五组收益','高因子组应在统计上优于低因子组')+groupTable+'</div>')+fmSection(5,'fm-backtest','组合净值与动态回撤','多头、基准与市场中性多空组合分开呈现。','<div class="fm-chart-grid">'+fmChartBox(ids.nav,'组合净值','信号收盘形成，首个可交易开盘执行')+fmChartBox(ids.dd,'动态回撤','相对各自历史峰值')+'</div>'+splitTable)+fmSection(6,'fm-annual','年度热力衰减','检查信号跨年度存活，而不是只看全窗均值。','<div class="fm-visual-grid">'+fmChartBox(ids.annual,'年度热力图','颜色按各指标研究尺度标准化')+annualTable+'</div>')+fmSection(7,'fm-walk','滚动样本外检验','冻结训练映射后逐年向前测试。','<div class="fm-visual-grid">'+fmChartBox(ids.walk,'滚动训练/样本外RankIC','同时显示训练到样本外衰减')+walkTable+'</div>')+fmSection(8,'fm-purged','隔离K折过拟合检验','剔除相邻调仓期，防止标签重叠与边界污染。','<div class="fm-visual-grid">'+fmChartBox(ids.purged,'隔离折RankIC','每折均使用冻结训练映射')+purgedTable+'</div>')+fmSection(9,'fm-regime','市场状态稳健性','状态由训练期趋势、波动、分散度和拥挤度定义。','<div class="fm-visual-grid">'+fmChartBox(ids.regime,'状态后验下界','验证与测试分开，零观测状态不冒充通过')+regimeTable+'</div>')+fmSection(10,'fm-incremental','独立增量与组合协同','同时残差化因子和收益，检查是否重复已有基线。','<div class="fm-visual-grid">'+fmChartBox(ids.incremental,'残差与边际贡献','验证用于搜索，测试仅最终报告')+incrementalTable+'</div>')+fmSection(11,'fm-overfit','多重检验与回测过拟合','DSR、PBO、CSCV、隔离折与新颖性联合审计。','<div class="fm-visual-grid">'+fmChartBox(ids.overfit,'置信与安全度','没有候选级证据时不伪造为0')+overfitTable+'</div>')+fmSection(12,'fm-attribution','行业、市值与流动性归因','识别因子是否只依赖单一行业或尾部股票。','<div class="fm-chart-grid">'+fmChartBox(ids.attribution,'行业RankIC前十','按样本内分层归因')+fmChartBox(ids.audit,'各集合覆盖率','训练、验证、测试和全窗')+'</div>'+attributionTable)+fmSection(13,'fm-audit','数据、执行与因果审计','确认点时数据、样本边界、标签隔离和可交易执行。',fmGateCards([{label:'数据覆盖',state:quick.coverage>=.8?'pass':'fail',note:fmPct(quick.coverage)},{label:'静态审计',state:fmObj(x.static_audit).passed===false?'fail':'pass',note:'无标签泄漏阻断项'},{label:'首个可交易开盘',state:'pass',note:'停牌与一字板约束'},{label:'测试集隔离',state:'pass',note:'不进入搜索父代选择'}])+auditTable)+'</div>');
    fmBindContext();fmDrawReport(x,ids,{splits:splits,ic:ic,groups:groups,curve:curve,annual:annual,walk:walk,purged:purged,regime:regime,incremental:incremental,overfit:overfit,attribution:attribution,audit:audit});
  }
  /* r21: factor score, evidence attribution and visible de-duplicated memory. */
  function fmMeanAvailable(values){
    const rows=arr(values).map(function(value){return fmFinite(value);}).filter(function(value){return value!==null;});
    return rows.length?rows.reduce(function(sum,value){return sum+value;},0)/rows.length:null;
  }
  function fmProbability(value){const number=fmFinite(value);return number===null?null:fmClamp(number,0,1);}
  function fmDimension(label,values,evidence,note){
    const value=fmMeanAvailable(values);
    return {label:label,score:value===null?null:value*100,evidence:evidence,note:note||''};
  }
  function fmScoreDimensions(x){
    const search=fmObj(x.posterior_search_evidence),final=fmObj(x.posterior_final_evidence);
    const research=fmObj(search.component_probabilities),sealed=fmObj(final.component_probabilities);
    const incremental=fmObj(x.incremental_evidence),validInc=fmObj(incremental.valid);
    const walk=fmObj(x.walk_forward),purged=fmObj(x.purged_kfold),validRegime=fmObj(fmObj(x.regime_evidence).valid);
    const antiValues=[x.test_long_short_deflated_sharpe_confidence];
    if(fmFinite(x.pbo_proxy)!==null)antiValues.push(1-Number(x.pbo_proxy));
    if(x.search_cscv_candidate_evidence_available&&fmFinite(x.search_cscv_probability_overfit_above_half)!==null)antiValues.push(1-Number(x.search_cscv_probability_overfit_above_half));
    return [
      fmDimension('研究期信号',[research.train_signal,research.validation_signal],'训练 '+fmPct(research.train_signal)+'；验证 '+fmPct(research.validation_signal),'只参与搜索与父代选择'),
      fmDimension('独立增量',[research.incremental_residual,fmObj(validInc.residual_posterior).positive_probability],'验证残差后验 '+fmPct(fmObj(validInc.residual_posterior).positive_probability),'剔除冻结基线后仍存在的信息'),
      fmDimension('组合协同',[research.downstream_synergy,fmObj(validInc.marginal_posterior).positive_probability],'验证边际后验 '+fmPct(fmObj(validInc.marginal_posterior).positive_probability),'加入冻结组合后的净贡献'),
      fmDimension('经济兑现',[research.economic_realization,sealed.test_economic_realization],'验证 '+fmPct(research.economic_realization)+'；封存测试 '+fmPct(sealed.test_economic_realization),'成本后多空收益兑现概率'),
      fmDimension('状态稳健',[research.regime_breadth,validRegime.posterior_positive_breadth],'正向广度 '+fmPct(validRegime.posterior_positive_breadth)+'；最弱下界 '+fmNum(validRegime.worst_state_lower_90),'训练期定义状态，验证期裁判'),
      fmDimension('样本外稳定',[walk.positive_rate,walk.positive_test_ic_ratio,purged.positive_rate,purged.positive_test_ic_ratio],'滚动正IC '+fmPct(fmFinite(walk.positive_rate,walk.positive_test_ic_ratio))+'；隔离折正IC '+fmPct(fmFinite(purged.positive_rate,purged.positive_test_ic_ratio)),'滚动窗口与隔离K折共同验证'),
      fmDimension('过拟合安全',antiValues,'DSR '+fmPct(x.test_long_short_deflated_sharpe_confidence)+'；PBO安全 '+(fmFinite(x.pbo_proxy)===null?'未形成':fmPct(1-Number(x.pbo_proxy))),'仅使用已形成的多重试验证据'),
      fmDimension('新颖与部署',[fmFinite(x.redundancy_max_abs_corr)===null?null:1-Number(x.redundancy_max_abs_corr),x.lifecycle_deployment_confidence],'新颖保留 '+(fmFinite(x.redundancy_max_abs_corr)===null?'未形成':fmPct(1-Number(x.redundancy_max_abs_corr)))+'；部署置信 '+fmPct(x.lifecycle_deployment_confidence),'避免复制父代并反映近期生命周期')
    ];
  }
  function fmScoreRows(x){
    return fmScoreDimensions(x).map(function(row){return {dimension:row.label,score:row.score===null?'--':fmSig(row.score,1,'分'),evidence:row.evidence,meaning:row.note};});
  }
  function fmRewardName(key){
    const map={
      sealed_search_evidence_probability:'封存前研究证据概率',test_signal_probability:'测试信号概率',
      test_incremental_residual_probability:'测试残差增量概率',test_downstream_synergy_probability:'测试组合贡献概率',
      test_economic_realization_probability:'测试收益兑现概率',joint_positive_probability:'最终联合后验概率',
      posterior_log_odds_utility:'后验赔率效用',novelty_log_prior:'新颖性先验',drawdown_log_prior:'回撤先验',
      train_signal_probability:'训练信号概率',validation_signal_probability:'验证信号概率',
      incremental_residual_probability:'验证残差增量概率',downstream_synergy_probability:'验证组合贡献概率',
      economic_realization_probability:'验证收益兑现概率',regime_breadth_probability:'状态广度概率',
      coverage_log_prior:'覆盖率先验',complexity_log_prior:'复杂度先验',posterior_utility:'搜索后验效用',
      train_signal:'训练信号',validation_signal:'验证信号',incremental_residual:'验证残差增量',
      downstream_synergy:'验证组合贡献',economic_realization:'验证收益兑现',regime_breadth:'验证状态广度',
      sealed_search_evidence:'封存前研究证据',test_signal:'测试信号',test_incremental_residual:'测试残差增量',
      test_downstream_synergy:'测试组合贡献',test_economic_realization:'测试收益兑现'
    };
    return map[key]||String(key||'').replace(/_/g,' ');
  }
  function fmPosteriorRows(x){
    const output=[],search=fmObj(x.posterior_search_evidence),final=fmObj(x.posterior_final_evidence);
    Object.keys(fmObj(search.component_probabilities)).forEach(function(key){
      const value=fmProbability(fmObj(search.component_probabilities)[key]);
      if(value!==null)output.push({stage:'训练+验证',component:fmRewardName(key),probability:value});
    });
    Object.keys(fmObj(final.component_probabilities)).forEach(function(key){
      const value=fmProbability(fmObj(final.component_probabilities)[key]);
      if(value!==null)output.push({stage:'封存测试报告',component:fmRewardName(key),probability:value});
    });
    return output;
  }
  function fmRewardRows(x){
    const breakdown=fmFirstObj(x.reward_breakdown,x.score_breakdown),rows=[];
    Object.keys(breakdown).forEach(function(key){
      const value=fmFinite(breakdown[key]);
      if(value!==null)rows.push({metric:fmRewardName(key),raw:key,value:value,kind:/probability/.test(key)?'概率':'效用'});
    });
    return rows.sort(function(a,b){return Math.abs(b.value)-Math.abs(a.value);});
  }
  function fmPressureRows(x){
    const pressures=fmObj(fmObj(x.posterior_search_evidence).failure_pressures);
    return Object.keys(pressures).map(function(key){return {pressure:fmRewardName(key)+'不足',value:fmFinite(pressures[key])};})
      .filter(function(row){return row.value!==null;}).sort(function(a,b){return b.value-a.value;});
  }
  function fmDrawScoreRadar(id,dimensions){
    const rows=dimensions.filter(function(row){return row.score!==null;});
    if(!rows.length){const host=$(id);if(host)host.innerHTML='<div class="fm-empty">当前结果没有维度证据。</div>';return;}
    const theta=rows.map(function(row){return row.label;}),radial=rows.map(function(row){return row.score;});
    theta.push(theta[0]);radial.push(radial[0]);
    plot(id,[{type:'scatterpolar',r:radial,theta:theta,fill:'toself',name:'证据分',line:{color:'#2f5f9f',width:2},fillcolor:'rgba(47,95,159,.16)'}],
      {height:390,polar:{radialaxis:{visible:true,range:[0,100],ticksuffix:'分',gridcolor:'#d9dee8'},angularaxis:{gridcolor:'#e5e7eb'}},showlegend:false,margin:{l:56,r:56,t:36,b:36}});
  }
  function fmDrawPosterior(id,rows){
    plot(id,[{type:'bar',orientation:'h',x:rows.map(function(row){return row.probability*100;}),y:rows.map(function(row){return row.stage+' · '+row.component;}),
      text:rows.map(function(row){return fmPct(row.probability);}),textposition:'auto',
      marker:{color:rows.map(function(row){return row.stage==='训练+验证'?'#276b58':'#2f5f9f';})}}],
      {height:Math.max(340,150+rows.length*30),showlegend:false,xaxis:{title:'正向后验概率',range:[0,100],ticksuffix:'%'},yaxis:{automargin:true},margin:{l:190,r:24,t:16,b:52}});
  }
  function fmDrawReward(id,rows){
    const utility=rows.filter(function(row){return row.kind==='效用';});
    if(!utility.length){const host=$(id);if(host)host.innerHTML='<div class="fm-empty">当前结果没有赔率效用拆解。</div>';return;}
    plot(id,[{type:'bar',orientation:'h',x:utility.map(function(row){return row.value;}),y:utility.map(function(row){return row.metric;}),
      text:utility.map(function(row){return fmNum(row.value);}),textposition:'auto',
      marker:{color:utility.map(function(row){return row.value>=0?'#276b58':'#9d3b3b';})}}],
      {height:Math.max(300,130+utility.length*42),showlegend:false,xaxis:{title:'对数赔率 / 先验效用',zeroline:true},yaxis:{automargin:true},margin:{l:150,r:24,t:16,b:52}});
  }
  function fmDrawPressure(id,rows){
    if(!rows.length){const host=$(id);if(host)host.innerHTML='<div class="fm-empty">该候选没有记录失败压力。</div>';return;}
    plot(id,[{type:'bar',orientation:'h',x:rows.map(function(row){return row.value*100;}),y:rows.map(function(row){return row.pressure;}),
      text:rows.map(function(row){return fmPct(row.value);}),textposition:'auto',marker:{color:'#b7791f'}}],
      {height:Math.max(300,130+rows.length*40),showlegend:false,xaxis:{title:'失败压力',range:[0,100],ticksuffix:'%'},yaxis:{automargin:true},margin:{l:145,r:24,t:16,b:52}});
  }
  function fmScoreSummary(x,dimensions){
    const available=dimensions.map(function(row){return row.score;}).filter(function(value){return value!==null;});
    const evidenceMean=available.length?available.reduce(function(sum,value){return sum+value;},0)/available.length:null;
    return [
      {label:'引擎最终状态',value:fmStatus(x.status),note:x.accepted_type_cn||'',tone:fmTone(x.status)},
      {label:'证据维度均值',value:evidenceMean===null?'--':fmSig(evidenceMean,1,'分'),note:'仅用于展示，不替代引擎裁判'},
      {label:'训练验证搜索效用',value:fmNum(fmFinite(x.search_risk_adjusted_selection_score,x.selection_score,fmObj(x.posterior_search_evidence).utility)),note:'测试集不参与'},
      {label:'最终报告奖励',value:fmNum(fmFinite(x.reward_score,x.reward,x.composite_score)),note:'包含封存测试报告'},
      {label:'联合研究后验',value:fmPct(fmFinite(fmObj(x.posterior_search_evidence).joint_positive_probability,x.posterior_joint_positive_probability)),note:'训练+验证'},
      {label:'最终联合后验',value:fmPct(fmFinite(fmObj(x.posterior_final_evidence).joint_positive_probability,x.posterior_final_joint_positive_probability)),note:'封存测试只报告'},
      {label:'生命周期',value:fmStatus(x.lifecycle_state),note:x.lifecycle_production_ready?'生产复核可用':'保持研究观察',tone:x.lifecycle_production_ready?'pass':'watch'},
      {label:'部署置信度',value:fmPct(x.lifecycle_deployment_confidence),note:x.lifecycle_production_ready_reason||''}
    ];
  }
  function fmCandidateScoreRows(){
    return reports().map(function(row,index){
      return {number:index+1,factor:row.chinese_name||row.name||row.factor||'未命名因子',status:fmStatus(row.status),channel:fmChannel(row.channel),
        train:fmNum(row.train_rank_ic),valid:fmNum(row.valid_rank_ic),test:fmNum(row.test_rank_ic),
        residual:fmNum(row.valid_incremental_residual_rank_ic),marginal:fmNum(row.valid_downstream_marginal_rank_ic_gain),
        annual:fmPct(row.test_long_short_annual_return),sharpe:fmNum(row.test_long_short_sharpe),
        drawdown:fmPct(row.test_long_short_max_drawdown),posterior:fmPct(row.posterior_final_joint_positive_probability),
        deployment:fmPct(row.lifecycle_deployment_confidence)};
    });
  }
  function fmDiagnosisHTML(x){
    const plan=arr(x.mutation_plan),weakest=fmObj(x.posterior_search_evidence).weakest_component;
    return '<div class="fm-diagnosis"><article><h3>训练验证搜索归因</h3><p>'+esc(x.search_diagnosis_cn||'未提供搜索阶段归因。')+'</p>'+
      '<dl><div><dt>最弱证据</dt><dd>'+esc(fmRewardName(weakest||'未形成'))+'</dd></div><div><dt>搜索诊断码</dt><dd>'+esc(x.search_diagnosis_code||'--')+'</dd></div></dl></article>'+
      '<article><h3>封存测试最终归因</h3><p>'+esc(x.diagnosis_cn||x.lifecycle_production_ready_reason||'未提供最终归因。')+'</p>'+
      '<dl><div><dt>通过类型</dt><dd>'+esc(x.accepted_type_cn||fmStatus(x.status))+'</dd></div><div><dt>生命周期</dt><dd>'+esc(fmStatus(x.lifecycle_state))+'</dd></div></dl></article>'+
      '<article><h3>智能变异方向</h3>'+(plan.length?'<ol>'+plan.map(function(item){return '<li>'+esc(item)+'</li>';}).join('')+'</ol>':'<p>该历史任务没有记录下一轮变异计划。</p>')+'</article></div>';
  }
  function fmStockDiagnostics(x){
    const all=fmObj(fResult().stock_diagnostics),factorKey=String(x.factor||x.name||'');
    return fmFirstObj(x.stock_diagnostics,all[factorKey],Object.keys(all).length===1?all[Object.keys(all)[0]]:{});
  }
  function fmUniverseName(value){
    return {ALL_A:'全A优质域',CSI800_ENH:'中证800',CSI2000_ENH:'中证2000（发布前方法学代理）'}[String(value||'')]||String(value||'--');
  }
  function fmFrequencyName(value){
    return {D:'日频',W:'周频',M:'月频',Q:'季频'}[String(value||'')]||String(value||'--');
  }
  function fmDiagnosticBlock(diagnostics,universe,frequency){
    return fmObj(fmObj(fmObj(diagnostics).results)[universe])[frequency]||{};
  }
  function fmDiagnosticMatrixRows(diagnostics){
    return arr(diagnostics.matrix).map(function(row){
      return {universe:row.universe_cn||fmUniverseName(row.universe),frequency:row.frequency_cn||fmFrequencyName(row.frequency),
        passed:row.passed?'通过':'未通过',train:fmNum(row.train_rank_ic),valid:fmNum(row.valid_rank_ic),
        test:fmNum(row.test_rank_ic),excess:fmPct(row.test_excess_annual_return),sharpe:fmNum(row.test_sharpe),
        drawdown:fmPct(row.test_max_drawdown)};
    });
  }
  function fmDiagnosticMetricItems(block){
    const metrics=fmObj(block.metrics),names={train:'训练期',valid:'验证期',test:'测试期',full:'全窗口'};
    return ['train','valid','test','full'].map(function(name){
      const row=fmObj(metrics[name]);
      return {label:names[name]+' RankIC',value:fmNum(row.rank_ic),
        note:'多头超额 '+fmPct(row.excess_annual_return)+' · 多空 '+fmPct(row.long_short_annual_return)+' · 夏普 '+fmNum(row.sharpe)+' · 回撤 '+fmPct(row.max_drawdown),
        tone:name==='test'?(row.rank_ic>0&&row.excess_annual_return>0?'pass':'watch'):''};
    });
  }
  function fmDiagnosticGateItems(block,diagnostics){
    const checks=fmObj(block.checks),labels={enough_periods:'分段样本充分',train_rank_ic:'训练RankIC',valid_rank_ic:'验证RankIC',test_rank_ic:'测试RankIC',
      valid_excess:'验证超额收益',test_excess:'测试超额收益',valid_test_same_direction:'验证测试同向',coverage:'样本覆盖率'};
    const rows=Object.keys(labels).map(function(key){return {label:labels[key],state:checks[key]?'pass':'fail',note:checks[key]?'满足诊断规则':'未满足诊断规则'};});
    const integrity=fmObj(diagnostics.integrity);
    rows.push({label:'正式切分一致',state:integrity.same_formal_factor_split?'pass':'fail',note:integrity.same_formal_factor_split?'复用因子主检验边界':'使用独立诊断边界'});
    rows.push({label:'测试集隔离',state:integrity.test_not_used_for_formula?'pass':'fail',note:'测试仅作最终报告'});
    if(block.universe==='CSI2000_ENH'){
      const membership=fmObj(fmObj(diagnostics.universe_membership_audit).CSI2000_ENH);
      rows.push({label:'成员口径可追溯',state:integrity.csi2000_prelaunch_proxy_is_explicitly_labeled?'pass':'fail',
        note:'发布前方法学代理，'+fmDateText(membership.official_start)+'起官方成分'});
    }
    return rows;
  }
  function fmStockDiagnosticsHTML(x){
    const diagnostics=fmStockDiagnostics(x);
    if(!Object.keys(diagnostics).length)return '<div class="fm-missing-evidence"><strong>该候选尚无股票级诊断</strong>'+
      '<p>只有通过严格因子门槛且完成点时策略后处理的因子才会出现三域四频、Top/Bottom 10 和个股策略结果。旧任务不会用组合曲线或模拟数据补齐；请选择带有诊断结果的最新通过因子。</p></div>';
    return '<div id="fm-stock-diagnostics"><div class="fm-loading">正在组织点时策略诊断...</div></div>';
  }
  function fmDrawDiagnosticBlock(block){
    const curve=arr(block.curve),dates=curve.map(function(row){return fmDate(row.date);});
    plot('fm-diag-nav',curve.length?[
      {type:'scatter',mode:'lines',name:'优质股多头净值',x:dates,y:curve.map(function(row){return row.long_nav;}),line:{color:'#3f66a5',width:2.2}},
      {type:'scatter',mode:'lines',name:'等权基准净值',x:dates,y:curve.map(function(row){return row.benchmark_nav;}),line:{color:'#8a8f98',width:1.8}},
      {type:'scatter',mode:'lines',name:'多空净值',x:dates,y:curve.map(function(row){return row.long_short_nav;}),line:{color:'#276b58',width:2.2}}
    ]:[],{hovermode:'x unified',legend:{orientation:'h',y:-.24},xaxis:{showgrid:false},yaxis:{title:'净值',gridcolor:'#edf0f2'}});
    plot('fm-diag-dd',curve.length?[
      {type:'scatter',mode:'lines',name:'多头回撤',x:dates,y:curve.map(function(row){return row.long_drawdown;}),line:{color:'#3f66a5',width:2}},
      {type:'scatter',mode:'lines',name:'基准回撤',x:dates,y:curve.map(function(row){return row.benchmark_drawdown;}),line:{color:'#8a8f98',width:1.6}},
      {type:'scatter',mode:'lines',name:'多空回撤',x:dates,y:curve.map(function(row){return row.long_short_drawdown;}),line:{color:'#a8483d',width:2}}
    ]:[],{hovermode:'x unified',legend:{orientation:'h',y:-.24},xaxis:{showgrid:false},yaxis:{title:'回撤',tickformat:'.1%',gridcolor:'#edf0f2',zerolinecolor:'#cfd6df'}});
    const icRows=arr(block.ic_series),splitColors={train:'#276b58',valid:'#3f66a5',test:'#a8483d'};
    const icTraces=['train','valid','test'].map(function(split){
      const rows=icRows.filter(function(row){return row.split===split;});
      return {type:'scatter',mode:'lines',name:{train:'训练期',valid:'验证期',test:'测试期'}[split],x:rows.map(function(row){return fmDate(row.date);}),
        y:rows.map(function(row){return row.rank_ic;}),line:{color:splitColors[split],width:1.8}};
    }).filter(function(trace){return trace.x.length;});
    plot('fm-diag-ic',icTraces,{hovermode:'x unified',legend:{orientation:'h',y:-.24},xaxis:{showgrid:false},yaxis:{title:'RankIC',gridcolor:'#edf0f2',zerolinecolor:'#98a2b3'}});
    const groups=arr(fmObj(fmObj(block.metrics).full).group_returns);
    plot('fm-diag-groups',groups.length?[{type:'bar',x:groups.map(function(_,index){return '第'+(index+1)+'组';}),y:groups,
      text:groups.map(function(value){return fmPct(value);}),textposition:'auto',marker:{color:['#b8d8cd','#91b9bd','#6d96ad','#527aa3','#3f66a5']}}]:[],
      {showlegend:false,xaxis:{showgrid:false},yaxis:{title:'平均持有期收益',tickformat:'.1%',gridcolor:'#edf0f2',zerolinecolor:'#98a2b3'}});
    const annual=arr(block.annual),years=annual.map(function(row){return String(row.year);});
    const annualZ=[annual.map(function(row){return row.rank_ic;}),annual.map(function(row){return row.excess_return;}),annual.map(function(row){return row.long_short_return;})];
    plot('fm-diag-annual',annual.length?[{type:'heatmap',x:years,y:['RankIC','多头超额','多空收益'],z:annualZ,
      colorscale:[[0,'#f1d7d2'],[.5,'#f7f9fb'],[1,'#4d7d6d']],zmid:0,colorbar:{title:'强度',thickness:10},hovertemplate:'%{x}<br>%{y}: %{z:.3f}<extra></extra>'}]:[],
      {height:280,xaxis:{side:'bottom',showgrid:false},yaxis:{automargin:true},margin:{l:80,r:50,t:16,b:40}});
  }
  function fmStockMetricItems(payload){
    const metrics=fmObj(payload.strategy_metrics),names={train:'训练期',valid:'验证期',test:'测试期',full:'全窗口'};
    return ['train','valid','test','full'].map(function(name){
      const row=fmObj(metrics[name]);
      return {label:names[name]+'策略年化',value:fmPct(row.annual_return),
        note:'相对买持 '+fmPct(row.excess_annual_return)+' · 夏普 '+fmNum(row.sharpe)+' · 回撤 '+fmPct(row.max_drawdown)+' · 交易 '+fmCount(row.trade_count)+' 次',
        tone:name==='test'?(row.excess_annual_return>0?'pass':'watch'):''};
    });
  }
  function fmDrawStockPayload(payload){
    const series=arr(payload.series),curve=arr(payload.strategy_curve),seriesDates=series.map(function(row){return fmDate(row[0]);});
    const priceTrace={type:'scatter',mode:'lines',name:'前复权股价',x:seriesDates,y:series.map(function(row){return row[2];}),line:{color:'#3f66a5',width:2},yaxis:'y'};
    const factorTrace={type:'scatter',mode:'lines',name:'因子横截面分位',x:seriesDates,y:series.map(function(row){return row[1];}),line:{color:'#276b58',width:1.7},yaxis:'y2'};
    const buys=series.filter(function(row){return row[4]==='买入';}),sells=series.filter(function(row){return row[4]==='卖出';});
    const traces=[priceTrace,factorTrace];
    if(buys.length)traces.push({type:'scatter',mode:'markers',name:'买入',x:buys.map(function(row){return fmDate(row[0]);}),y:buys.map(function(row){return row[2];}),marker:{symbol:'triangle-up',size:10,color:'#a8483d'},yaxis:'y'});
    if(sells.length)traces.push({type:'scatter',mode:'markers',name:'卖出',x:sells.map(function(row){return fmDate(row[0]);}),y:sells.map(function(row){return row[2];}),marker:{symbol:'triangle-down',size:10,color:'#276b58'},yaxis:'y'});
    plot('fm-stock-factor-price',series.length?traces:[],{hovermode:'x unified',legend:{orientation:'h',y:-.25},xaxis:{showgrid:false},
      yaxis:{title:'前复权股价',gridcolor:'#edf0f2'},yaxis2:{title:'因子分位',overlaying:'y',side:'right',range:[0,1],tickformat:'.0%',showgrid:false}});
    const curveDates=curve.map(function(row){return fmDate(row[0]);});
    plot('fm-stock-nav',curve.length?[
      {type:'scatter',mode:'lines',name:'因子策略净值',x:curveDates,y:curve.map(function(row){return row[1];}),line:{color:'#276b58',width:2.2}},
      {type:'scatter',mode:'lines',name:'买入持有净值',x:curveDates,y:curve.map(function(row){return row[2];}),line:{color:'#3f66a5',width:1.8}}
    ]:[],{hovermode:'x unified',legend:{orientation:'h',y:-.24},xaxis:{showgrid:false},yaxis:{title:'净值',gridcolor:'#edf0f2'}});
    plot('fm-stock-dd',curve.length?[
      {type:'scatter',mode:'lines',name:'因子策略回撤',x:curveDates,y:curve.map(function(row){return row[3];}),line:{color:'#a8483d',width:2}},
      {type:'scatter',mode:'lines',name:'买入持有回撤',x:curveDates,y:curve.map(function(row){return row[4];}),line:{color:'#8a8f98',width:1.8}}
    ]:[],{hovermode:'x unified',legend:{orientation:'h',y:-.24},xaxis:{showgrid:false},yaxis:{title:'回撤',tickformat:'.1%',gridcolor:'#edf0f2',zerolinecolor:'#98a2b3'}});
  }
  function fmRenderStockPayload(payload){
    const host=$('fm-stock-detail');if(!host)return;
    const policy=fmObj(payload.signal_policy);
    host.innerHTML='<div class="fm-stock-heading"><div><span class="fm-badge pass">点时回测</span><h3>'+esc(payload.ts_code||'--')+' '+esc(payload.stock_name||'')+'</h3>'+
      '<p>'+esc(payload.universe_cn||fmUniverseName(payload.universe))+' · '+esc(payload.frequency_cn||fmFrequencyName(payload.frequency))+'；信号收盘形成，下一可交易开盘执行。</p></div></div>'+
      fmMetricCards(fmStockMetricItems(payload))+
      '<div class="fm-chart-grid">'+fmChartBox('fm-stock-factor-price','个股因子时序与股价','右轴为横截面因子分位；三角标记真实可执行买卖点')+
      fmChartBox('fm-stock-nav','个股因子策略净值','与同一股票买入持有对照')+'</div>'+
      '<div class="fm-visual-grid">'+fmChartBox('fm-stock-dd','个股策略与买持回撤','严格按各自历史峰值计算')+
      '<div class="fm-policy"><h3>单股策略契约</h3><dl><div><dt>入场分位</dt><dd>'+fmPct(policy.entry_threshold)+'</dd></div>'+
      '<div><dt>退出分位</dt><dd>'+fmPct(policy.exit_threshold)+'</dd></div><div><dt>单边换手成本</dt><dd>'+fmPct(policy.cost_rate_per_turnover)+'</dd></div>'+
      '<div><dt>执行时点</dt><dd>信号收盘后下一可交易开盘</dd></div><div><dt>成分股口径</dt><dd>信号日点时成员</dd></div>'+
      '<div><dt>集合初始化</dt><dd>训练、验证、测试分别清仓起算</dd></div><div><dt>隔离区间</dt><dd>不评价，也不更新持仓</dd></div>'+
      '<div><dt>边界标签</dt><dd>直接跨集合标签剔除</dd></div></dl></div></div>';
    fmDrawStockPayload(payload);
  }
  async function fmLoadStockDiagnostics(x,diagnostics,universe,frequency,code){
    const host=$('fm-stock-detail');if(!host)return;
    const factor=String(x.factor||x.name||diagnostics.factor||''),job=String(S.factor.selectedJob||'');
    const key=[job,factor,universe,frequency,code].join('|');
    if(S.factor.stockPayloadKey===key&&S.factor.stockPayload){fmRenderStockPayload(S.factor.stockPayload);return;}
    host.innerHTML='<div class="fm-loading">正在读取 '+esc(code)+' 的真实因子与交易序列...</div>';
    const path='/api/factor/history/'+encodeURIComponent(job)+'/stock?factor='+encodeURIComponent(factor)+'&code='+encodeURIComponent(code)+
      '&universe='+encodeURIComponent(universe)+'&frequency='+encodeURIComponent(frequency);
    const token=key+'|'+Date.now();S.factor.stockRequestToken=token;
    try{
      fmDropCache(path);const response=await api(path);
      if(S.factor.stockRequestToken!==token)return;
      S.factor.stockPayloadKey=key;S.factor.stockPayload=fmObj(response.result);fmRenderStockPayload(S.factor.stockPayload);
    }catch(error){
      if(S.factor.stockRequestToken!==token)return;
      host.innerHTML='<div class="fm-missing-evidence"><strong>个股诊断读取失败</strong><p>'+esc(error.message)+'</p></div>';
    }
  }
  function fmRenderStockDiagnosticsPanel(x){
    const host=$('fm-stock-diagnostics'),diagnostics=fmStockDiagnostics(x);if(!host||!Object.keys(diagnostics).length)return;
    const universes=['ALL_A','CSI800_ENH','CSI2000_ENH'],frequencies=['D','W','M','Q'];
    let universe=universes.includes(S.factor.stockUniverse)?S.factor.stockUniverse:'ALL_A';
    let frequency=frequencies.includes(S.factor.stockFrequency)?S.factor.stockFrequency:'W';
    let block=fmDiagnosticBlock(diagnostics,universe,frequency);
    if(!Object.keys(block).length){universe='ALL_A';frequency='W';block=fmDiagnosticBlock(diagnostics,universe,frequency);}
    const options=arr(diagnostics.stock_options),top=arr(block.top10);
    let code=String(S.factor.stockCode||((top[0]||{}).ts_code)||((options[0]||{}).ts_code)||'');
    S.factor.stockUniverse=universe;S.factor.stockFrequency=frequency;S.factor.stockCode=code;
    const matrix=fmSimpleTable('三股票池 × 四调仓频率总表',fmDiagnosticMatrixRows(diagnostics),[
      {key:'universe',label:'选股域'},{key:'frequency',label:'调仓频率'},{key:'passed',label:'诊断状态'},
      {key:'train',label:'训练RankIC'},{key:'valid',label:'验证RankIC'},{key:'test',label:'测试RankIC'},
      {key:'excess',label:'测试超额年化'},{key:'sharpe',label:'测试多头夏普'},{key:'drawdown',label:'测试最大回撤'}]);
    const topTable=fmSimpleTable('最新 Top 10',top.map(function(row){return {rank:row.rank,code:row.ts_code,name:row.stock_name,industry:row.industry_name,score:fmPct(row.factor_score),tradable:row.entry_eligible===true?'可交易':row.entry_eligible===false?'交易受限':'待下一交易日'};}),[
      {key:'rank',label:'排名'},{key:'code',label:'代码'},{key:'name',label:'股票'},{key:'industry',label:'行业'},{key:'score',label:'因子分位'},{key:'tradable',label:'执行状态'}]);
    const bottomTable=fmSimpleTable('最新 Bottom 10',arr(block.bottom10).map(function(row){return {rank:row.rank,code:row.ts_code,name:row.stock_name,industry:row.industry_name,score:fmPct(row.factor_score),tradable:row.entry_eligible===true?'可交易':row.entry_eligible===false?'交易受限':'待下一交易日'};}),[
      {key:'rank',label:'排名'},{key:'code',label:'代码'},{key:'name',label:'股票'},{key:'industry',label:'行业'},{key:'score',label:'因子分位'},{key:'tradable',label:'执行状态'}]);
    const split=fmObj(diagnostics.split),windowText=fmDateText(diagnostics.start)+' 至 '+fmDateText(diagnostics.end);
    const formalSplit=diagnostics.split_source==='formal_factor_result';
    const widths=arr(diagnostics.portfolio_widths).map(function(row){return fmPct(row.fraction)+'（权重 '+fmPct(row.weight)+'）';}).join(' / ');
    const splitDescription=formalSplit?
      '沿用因子主检验的统一边界：训练 '+fmDateText(fmObj(split.train).start)+'—'+fmDateText(fmObj(split.train).end)+'，验证 '+fmDateText(fmObj(split.valid).start)+'—'+fmDateText(fmObj(split.valid).end)+'，测试 '+fmDateText(fmObj(split.test).start)+'—'+fmDateText(fmObj(split.test).end)+'。':
      '该任务缺少正式切分审计，因此使用独立诊断切分。';
    const membership=fmObj(fmObj(diagnostics.universe_membership_audit).CSI2000_ENH);
    const universeDescription=universe==='CSI2000_ENH'?
      '中证2000在 '+fmDateText(membership.official_start)+' 前按官方V1.1方法学做点时代理，之后使用Wind进出事件与完整权重快照；发布前结果不是官方指数历史。 ':'';
    host.innerHTML='<div class="fm-diagnostic-note"><strong>策略诊断窗口 '+esc(windowText)+'</strong><span>'+esc(universeDescription+splitDescription)+' 方向和组合宽度只由训练期冻结，测试仅作报告。四种频率共用同一个月频 DSL 状态；日/周/月/季只改变调仓节奏，不改写因子的经济窗口。</span></div>'+
      matrix+'<div class="fm-diagnostic-controls"><label>选股域<select id="fm-diag-universe">'+universes.map(function(value){return '<option value="'+value+'" '+(value===universe?'selected':'')+'>'+fmUniverseName(value)+'</option>';}).join('')+'</select></label>'+
      '<label>调仓频率<select id="fm-diag-frequency">'+frequencies.map(function(value){return '<option value="'+value+'" '+(value===frequency?'selected':'')+'>'+fmFrequencyName(value)+'</option>';}).join('')+'</select></label>'+
      '<label>个股代码<input id="fm-diag-stock" list="fm-diag-stock-options" value="'+esc(code)+'" autocomplete="off"><datalist id="fm-diag-stock-options">'+
      options.map(function(row){return '<option value="'+esc(row.ts_code)+'" label="'+esc(row.stock_name||'')+'"></option>';}).join('')+'</datalist></label>'+
      '<button id="fm-diag-load" class="action-button" type="button">查看个股</button></div>'+
      '<div class="fm-meta-row"><span>选中：'+esc(fmUniverseName(universe))+'</span><span>'+esc(fmFrequencyName(frequency))+'</span><span>排名时点：'+esc(fmDateText(block.latest_signal_date))+'</span><span>策略状态：'+(block.passed?'通过':'未通过')+'</span>'+
      '<span>成本：'+fmPct(diagnostics.cost_rate_per_turnover)+'/单边换手</span><span>训练冻结宽度：'+esc(widths||fmPct(diagnostics.portfolio_selected_fraction))+'</span>'+
      '<span>切分来源：'+(formalSplit?'主检验正式边界':'独立诊断边界')+'</span><span>训练边界：'+esc(fmDateText(fmObj(split.train).end))+'</span><span>测试起点：'+esc(fmDateText(fmObj(split.test).start))+'</span></div>'+
      fmGateCards(fmDiagnosticGateItems(block,diagnostics))+fmMetricCards(fmDiagnosticMetricItems(block))+
      '<div class="fm-chart-grid">'+fmChartBox('fm-diag-nav','选股组合净值','多头、等权基准与多空组合')+fmChartBox('fm-diag-dd','组合回撤','逐期成本后净值的峰值回撤')+'</div>'+
      '<div class="fm-chart-grid">'+fmChartBox('fm-diag-ic','RankIC 时序','训练、验证、测试使用不同颜色')+fmChartBox('fm-diag-groups','五组分组收益','检验因子单调性与头尾差异')+'</div>'+
      '<div class="fm-visual-grid">'+fmChartBox('fm-diag-annual','年度热力衰减','逐年 RankIC、超额和多空收益')+'<div class="fm-rank-tables">'+topTable+bottomTable+'</div></div>'+
      '<div id="fm-stock-detail" class="fm-stock-detail"></div>';
    fmDrawDiagnosticBlock(block);
    const universeSelect=$('fm-diag-universe'),frequencySelect=$('fm-diag-frequency'),stockInput=$('fm-diag-stock'),loadButton=$('fm-diag-load');
    if(universeSelect)universeSelect.onchange=function(){S.factor.stockUniverse=this.value;const next=fmDiagnosticBlock(diagnostics,this.value,frequency);S.factor.stockCode=String(((arr(next.top10)[0]||{}).ts_code)||code);S.factor.stockPayload=null;fmRenderStockDiagnosticsPanel(x);};
    if(frequencySelect)frequencySelect.onchange=function(){S.factor.stockFrequency=this.value;const next=fmDiagnosticBlock(diagnostics,universe,this.value);S.factor.stockCode=String(((arr(next.top10)[0]||{}).ts_code)||code);S.factor.stockPayload=null;fmRenderStockDiagnosticsPanel(x);};
    async function loadSelected(){const value=String((stockInput||{}).value||'').trim().toUpperCase();if(!value)return;S.factor.stockCode=value;await fmLoadStockDiagnostics(x,diagnostics,universe,frequency,value);}
    if(loadButton)loadButton.onclick=loadSelected;
    if(stockInput)stockInput.onkeydown=function(event){if(event.key==='Enter'){event.preventDefault();loadSelected();}};
    if(code)fmLoadStockDiagnostics(x,diagnostics,universe,frequency,code);
  }
  function factorScore(){
    const x=selectedFactor(),dimensions=fmScoreDimensions(x),posterior=fmPosteriorRows(x),reward=fmRewardRows(x),pressures=fmPressureRows(x);
    const ids={radar:pid('fmsr'),posterior:pid('fmspost'),reward:pid('fmsrew'),pressure:pid('fmspress')};
    conclusion('当前因子 '+esc(x.chinese_name||x.name||'--')+'，六维综合证据均值 '+fmNum(dimensions.reduce(function(sum,row){return sum+Number(row.value||0);},0)/Math.max(dimensions.length,1))+' 分，状态 '+esc(fmStatus(x.status||x.lifecycle_state))+'。');
    const dimensionTable=fmSimpleTable('证据维度打分表',fmScoreRows(x),[
      {key:'dimension',label:'维度'},{key:'score',label:'证据分'},{key:'evidence',label:'直接证据'},{key:'meaning',label:'研究含义'}]);
    const posteriorTable=fmSimpleTable('后验组件明细',posterior,[
      {key:'stage',label:'阶段'},{key:'component',label:'证据组件'},{label:'正向概率',value:function(row){return fmPct(row.probability);}}]);
    const rewardTable=fmSimpleTable('引擎奖励拆解',reward,[
      {key:'metric',label:'组件'},{label:'原始值',value:function(row){return row.kind==='概率'?fmPct(row.value):fmNum(row.value);}},{key:'kind',label:'量纲'}]);
    const candidateTable=fmSimpleTable('候选因子横向比较',fmCandidateScoreRows(),[
      {key:'number',label:'编号'},{key:'factor',label:'因子'},{key:'status',label:'状态'},{key:'channel',label:'搜索通道'},
      {key:'train',label:'训练RankIC'},{key:'valid',label:'验证RankIC'},{key:'test',label:'测试RankIC'},
      {key:'residual',label:'验证残差'},{key:'marginal',label:'验证边际'},{key:'annual',label:'多空年化'},
      {key:'sharpe',label:'多空夏普'},{key:'drawdown',label:'最大回撤'},{key:'posterior',label:'最终后验'},{key:'deployment',label:'部署置信'}]);
    root('<div class="fm-page">'+fmContextHTML()+
      fmSection(1,'fm-score-overview','综合裁判与维度打分','先呈现引擎状态与直接证据，再解释每个维度为何得分。',
        fmGateCards(fmGates(x))+fmMetricCards(fmScoreSummary(x,dimensions))+'<div class="fm-visual-grid">'+
        fmChartBox(ids.radar,'多维证据雷达','由真实概率、稳健性和生命周期字段归一化')+dimensionTable+'</div>')+
      fmSection(2,'fm-score-posterior','搜索后验与封存测试后验','绿色为训练验证搜索证据；蓝色为封存测试报告，后者绝不反馈父代选择。',
        '<div class="fm-chart-grid">'+fmChartBox(ids.posterior,'后验组件概率','每个组件独立展示，不用单一阈值掩盖弱项')+
        fmChartBox(ids.reward,'赔率效用与先验','正值增加奖励，负值体现复杂度、新颖性或回撤惩罚')+'</div>'+posteriorTable+rewardTable)+
      fmSection(3,'fm-score-diagnosis','失败归因与智能变异','根据训练验证最弱证据定位下一轮改写方向。',
        '<div class="fm-visual-grid">'+fmChartBox(ids.pressure,'搜索失败压力','数值越高代表越应优先修复')+fmDiagnosisHTML(x)+'</div>')+
      fmSection(4,'fm-score-candidates','候选因子横向比较','同一任务所有候选使用一致口径，测试结果仅用于最终报告。',
        candidateTable+fmCandidateCards(reports(),20))+
      fmSection(5,'fm-score-stocks','个股诊断与策略验证','该板块只呈现真实序列化的个股和策略数据，不以组合级结果替代。',
        fmStockDiagnosticsHTML(x))+'</div>');
    fmBindContext();fmBindCandidateCards();
    fmDrawScoreRadar(ids.radar,dimensions);fmDrawPosterior(ids.posterior,posterior);
    fmDrawReward(ids.reward,reward);fmDrawPressure(ids.pressure,pressures);fmRenderStockDiagnosticsPanel(x);
  }
  function fmMemoryCards(rows){
    if(!rows.length)return '<div class="fm-empty">暂无可查询的历史任务。</div>';
    return '<div class="fm-memory-list">'+rows.map(function(row){
      const searchable=[row.job_id,row.created_at,row.universe,row.status,row.source_label].join(' ').toLowerCase();
      return '<article class="fm-memory-card '+(String(row.job_id)===String(S.factor.selectedJob)?'selected':'')+
        '" data-fm-memory-card data-search="'+esc(searchable)+'" data-status="'+esc(String(row.status||'').toLowerCase())+'">'+
        '<header><div><span class="fm-badge '+fmTone(row.status)+'">'+esc(fmStatus(row.status))+'</span>'+
        '</div><time>'+esc(row.created_at||'--')+'</time></header>'+
        '<h3>'+esc(valueText(row.universe||'ALL_A'))+' · '+esc(String(row.job_id||''))+'</h3><dl>'+
        '<div><dt>样本行</dt><dd>'+fmCount(row.rows)+'</dd></div><div><dt>月份</dt><dd>'+fmCount(row.months)+'</dd></div>'+
        '<div><dt>候选</dt><dd>'+fmCount(row.candidate_count)+'</dd></div><div><dt>通过</dt><dd>'+fmCount(row.accepted_count)+'</dd></div>'+
        '<div><dt>目标</dt><dd>'+fmCount(row.target_accepted)+'</dd></div><div><dt>耗时</dt><dd>'+fmDuration(row.elapsed_seconds)+'</dd></div></dl>'+
        '<button type="button" class="ghost-button" data-fm-load="'+esc(row.job_id)+'">'+
        (String(row.job_id)===String(S.factor.selectedJob)?'重新载入':'载入并查看')+'</button></article>';
    }).join('')+'</div>';
  }
  function fmApplyMemoryFilter(){
    const query=String(($('fm-memory-query')||{}).value||'').trim().toLowerCase();
    const status=String(($('fm-memory-status')||{}).value||'all');let visible=0;
    document.querySelectorAll('[data-fm-memory-card]').forEach(function(card){
      const show=(!query||String(card.dataset.search||'').includes(query))&&(status==='all'||String(card.dataset.status||'')===status);
      card.hidden=!show;if(show)visible++;
    });
    setText('fm-memory-visible',fmCount(visible)+' 个任务');
  }
  async function factorMemory(){
    await needFactor(false);
    header('历史记忆','历史记忆','LLM因子挖掘');
    const rows=fRows();
    const completed=rows.filter(function(row){return ['done','completed'].includes(String(row.status||'').toLowerCase());}).length;
    const accepted=rows.filter(function(row){return Number(row.accepted_count||0)>0;}).length;
    const failed=rows.filter(function(row){return String(row.status||'').toLowerCase()==='failed';}).length;
    clearConclusion();
    root('<div class="fm-page">'+fmMetricCards([
      {label:'任务总数',value:fmCount(rows.length)},
      {label:'已完成',value:fmCount(completed)},
      {label:'含通过因子',value:fmCount(accepted)},
      {label:'失败任务',value:fmCount(failed)}])+
      '<section class="fm-section"><div class="fm-section-head"><span>01</span><div><h2>历史任务查询</h2></div></div>'+
      '<div class="fm-memory-toolbar"><label>搜索<input id="fm-memory-query" type="search" placeholder="任务编号 / 日期 / 股票池"></label>'+
      '<label>状态<select id="fm-memory-status"><option value="all">全部状态</option><option value="done">完成</option>'+
      '<option value="running">运行中</option><option value="queued">排队中</option><option value="failed">失败</option></select></label>'+
      '<button id="fm-memory-refresh" class="ghost-button" type="button">刷新历史</button>'+
      '<strong id="fm-memory-visible">'+fmCount(rows.length)+' 个任务</strong></div>'+fmMemoryCards(rows)+'</section></div>');
    fmBindHistoryLoad('factor:expression');
    if($('fm-memory-query'))$('fm-memory-query').oninput=fmApplyMemoryFilter;
    if($('fm-memory-status'))$('fm-memory-status').onchange=fmApplyMemoryFilter;
    if($('fm-memory-refresh'))$('fm-memory-refresh').onclick=async function(){
      this.disabled=true;try{S.factor.historyLoadedAt=0;await factorMemory();}finally{this.disabled=false;}
    };
  }
  async function renderFactor(view){
    const heading=HEAD['factor:'+view]||HEAD['factor:home'];
    header(heading[0],heading[1],'LLM因子挖掘');
    setText('as-of','真实历史与按需任务');
    setText('generated-at',String((S.factor.detail||{}).created_at||'实时刷新'));
    if(view==='home')return await factorHome();
    if(view==='memory')return await factorMemory();
    await factorDetail();
    if(!reports().length){
      conclusion('当前选中任务没有可展示的候选因子，请到历史记忆载入一个已完成任务，或在主页重新发起挖掘。');
      root('<div class="fm-page"><div class="fm-missing-evidence"><strong>没有候选因子结果</strong>'+
        '<p>任务可能仍在排队、运行、失败，或历史记录只保存了摘要。</p></div>'+fmRecentHistory(fRows(),8)+'</div>');
      fmBindHistoryLoad('factor:expression');return;
    }
    if(view==='expression')return factorExpression();
    if(view==='report')return factorReport();
    if(view==='score')return factorScore();
  }
/* Asset allocation r23: individual factor signals, full cycle atlas and equal-weight-relative backtest. */
  ALLOC_STRATEGY_CN.dual_momentum='趋势增强';
  Object.assign(COL,{annual_excess_return:'年化超额',tracking_error:'跟踪误差',information_ratio:'信息比率',active_month_hit_rate:'主动月胜率',
    max_relative_drawdown:'最大相对回撤',total_excess_return:'累计超额',train_excess:'训练年化超额',validation_excess:'验证年化超额',
    test_excess_report_only:'测试年化超额（仅报告）',validation_information_ratio:'验证信息比率',family:'模型族'});
  if(!arr(S.allocation.backtest).includes('equal_weight'))S.allocation.backtest=['recommended','equal_weight'].concat(arr(S.allocation.backtest).filter(function(k){return !['recommended','equal_weight'].includes(k);}));

  function allocWeightRows(data){ return ['recommended','equal_weight','dual_momentum','all_weather','risk_parity','hrp','macro_risk_budget','robust_bl','cycle_risk_parity','hmm_risk_parity'].map(function(strategy){const w=allocWeights(data,strategy,'equity_preferred');return {strategy:ALLOC_STRATEGY_CN[strategy],equity:allocPct(w.equity),bond:allocPct(w.bond),commodity:allocPct(w.commodity),cash:allocPct(w.cash)};}); }
  function allocAuditRows(data){const audit=obj(data.optimization),spec=obj(audit.selected_spec),gate=obj(audit.promotion_gate);return [
    {parameter:'候选规格',value:spec.id||'--'},{parameter:'模型族',value:spec.family==='equity_preferred_dual_momentum'?'权益偏好趋势增强':spec.family||'--'},
    {parameter:'战略锚',value:arr(spec.prior).map(function(v){return allocPct(v);}).join(' / ')},{parameter:'动量窗口',value:arr(spec.horizons).join(' / ')+'个月'},
    {parameter:'动量权重',value:arr(spec.horizon_weights).map(function(v){return allocPct(v);}).join(' / ')},{parameter:spec.probability_slope!=null?'概率斜率 / 宏观强度':'趋势强度 / 宏观强度',value:allocNumber(spec.probability_slope!=null?spec.probability_slope:spec.strength)+' / '+allocNumber(spec.macro_strength)},
    {parameter:'候选数量',value:Math.round(Number(audit.trial_count)||0)},{parameter:'CSCV-PBO',value:allocPct(audit.pbo_cscv)},
    {parameter:'验证期年化超额',value:allocPct(obj(audit.validation_active_metrics).annual_excess_return)},{parameter:'测试期年化超额（仅报告）',value:allocPct(obj(audit.test_active_metrics_report_only).annual_excess_return)},
    {parameter:'上线门槛',value:gate.status==='passed'?'通过':'条件性输出'}];}

  function allocCycleDefinitionsV3(data,model,currentCode){const definition=obj(obj(data.cycle_definitions)[model]),states=arr(definition.states);return '<section class="allocation-cycle-atlas"><header><span>周期阶段图谱</span><strong>'+esc(definition.name||'--')+'</strong></header><div class="allocation-definition-grid">'+states.map(function(state){const active=String(state.code)===String(currentCode);return '<article class="allocation-definition-card'+(active?' is-current':'')+'"><div><b>'+esc(String(state.order).padStart(2,'0'))+'</b><strong>'+esc(state.name)+'</strong></div><p>'+esc(state.summary)+'</p><small>'+esc(state.asset_bias)+'</small></article>';}).join('')+'</div></section>';}
  function allocCycleCurrentCodeV3(model,row){return model==='pring'?String(row.pring_phase||''):String(row[allocCycleSpec(model).state]||'');}
  function allocCycleFactorsV3(data,spec){const result=[],seen=new Set(),roles=obj(obj(data.factor_selection).roles);spec.roles.forEach(function(role){arr(roles[role]).forEach(function(factor){if(seen.has(factor.id))return;seen.add(factor.id);result.push({role:role,factor:factor});});});return result;}
  function allocFactorSignalShapesV3(rows){const shapes=[];if(!rows.length)return shapes;let start=0;for(let i=1;i<=rows.length;i++)if(i===rows.length||Number(rows[i].signal_state)!==Number(rows[start].signal_state)){const state=Number(rows[start].signal_state);if(state)shapes.push({type:'rect',xref:'x',yref:'paper',x0:allocMonth(rows[start].month)+'-01',x1:allocMonth(rows[Math.min(i,rows.length-1)].month)+'-28',y0:0,y1:1,fillcolor:state>0?'rgba(237,125,49,.18)':'rgba(47,117,181,.10)',line:{width:0},layer:'below'});start=i;}return shapes;}
  function allocFactorSignalChartV3(id,data,factor,lookback){let rows=arr(obj(data.factor_series)[factor.id]);if(lookback!=='all')rows=rows.slice(-Number(lookback));const x=rows.map(function(r){return allocMonth(r.month)+'-01';}),values=rows.map(function(r){return r.value==null?null:Number(r.value);}),probability=rows.map(function(r){return r.positive_probability==null?null:Number(r.positive_probability)*100;});
    plot(id,[{type:'scatter',mode:'lines',name:'标准化因子',x:x,y:values,line:{color:'#c00000',width:2.4},hovertemplate:'%{x|%Y-%m}<br>因子值 %{y:.2f}<extra></extra>'},{type:'scatter',mode:'lines',name:'上行概率',x:x,y:probability,yaxis:'y2',line:{color:'#7f8c8d',width:1.2,dash:'dot'},hovertemplate:'%{x|%Y-%m}<br>上行概率 %{y:.2f}%<extra></extra>'}],{height:315,hovermode:'x unified',shapes:allocFactorSignalShapesV3(rows).concat([{type:'line',xref:'paper',x0:0,x1:1,y0:0,y1:0,line:{color:'#98a2b3',width:1}}]),yaxis:{title:'标准化得分',zeroline:false},yaxis2:{title:'上行概率（%）',overlaying:'y',side:'right',range:[0,100],showgrid:false},xaxis:{type:'date',rangeslider:allocRangeSlider()},legend:{orientation:'h',y:-.30},margin:{l:55,r:55,t:20,b:70}});}
  function allocCycleTimelineV3(id,data,model,lookback){let rows=arr(obj(data.cycle_state_series)[model]);if(lookback!=='all')rows=rows.slice(-Number(lookback));const states=arr(obj(obj(data.cycle_definitions)[model]).states),ticks=states.map(function(s){return Number(s.order);}),labels=states.map(function(s){return s.name;});plot(id,[{type:'scatter',mode:'lines+markers',name:'周期划分',x:rows.map(function(r){return allocMonth(r.month)+'-01';}),y:rows.map(function(r){return Number(r.state_order);}),line:{color:'#c00000',width:12,shape:'hv'},marker:{size:3,color:'#c00000'},customdata:rows.map(function(r){return [r.state_name,Number(r.confidence)*100];}),hovertemplate:'%{x|%Y-%m}<br>%{customdata[0]}<br>置信度 %{customdata[1]:.2f}%<extra></extra>'}],{height:360,hovermode:'x unified',yaxis:{title:'阶段',tickmode:'array',tickvals:ticks,ticktext:labels,range:[.4,Math.max.apply(null,ticks)+.6]},xaxis:{type:'date',rangeslider:allocRangeSlider()},showlegend:false,margin:{l:100,r:35,t:20,b:65}});}

  async function allocationCycle(){
    const data=await needAllocation(),model=S.allocation.cycleModel,spec=allocCycleSpec(model),lookback=S.allocation.lookback;let history=arr(data.cycle_history);if(lookback!=='all')history=history.slice(-Number(lookback));const current=history[history.length-1]||{},currentCode=allocCycleCurrentCodeV3(model,current),factors=allocCycleFactorsV3(data,spec);
    header('周期跟踪','逐因子信号、完整阶段图谱与历史复盘','资产配置');setText('as-of',allocMonth(obj(data.data_as_of).macro_complete));setText('generated-at',String(data.generated_at||'--').replace('T',' ').replace('Z',''));
    conclusion('当前 '+spec.title+'：'+esc(model==='pring'?'阶段'+current.pring_phase+' · '+current.pring_phase_name:current[spec.state]||'--')+'，置信度 '+allocPct(allocConfidence(current,model,spec))+'；每个因子独立展示原值、因果上行概率和信号区间。');
    const controls='<section class="control-card"><div class="control-grid allocation-cycle-controls"><label>周期模型<select id="alloc-cycle-model"><option value="pring">普林格六阶段</option><option value="kitchin">基钦周期</option><option value="juglar">朱格拉周期</option><option value="kondratieff">康波情景</option><option value="merrill">美林时钟</option></select></label><label>观察窗口<select id="alloc-cycle-lookback"><option value="60">近5年</option><option value="120">近10年</option><option value="all">全部</option></select></label><div class="control-readout">当前状态<strong>'+esc(model==='pring'?'阶段'+current.pring_phase+' · '+current.pring_phase_name:current[spec.state]||'--')+'</strong></div><div class="control-readout">独立因子图<strong>'+factors.length+' 张</strong></div></div></section>';
    const chartIds=factors.map(function(){return pid('acf');}),timeline=pid('act');const factorPanels='<div class="allocation-factor-grid">'+factors.map(function(item,index){const f=item.factor;return panel(chartIds[index],f.name,item.role+' · 训练IC '+allocNumber(f.train_ic)+' · 综合分 '+allocNumber(f.score),true);}).join('')+'</div>';
    const rows=arr(obj(data.cycle_state_series)[model]).slice().reverse().map(function(r){return {month:allocMonth(r.month),state:r.state_name,confidence:allocPct(r.confidence)};});const selected=allocSelectedFactors(data,spec.roles);
    root(controls+allocCycleDefinitionsV3(data,model,currentCode)+factorPanels+panel(timeline,spec.title+'历史阶段总图','阶梯表示每月归属；仅使用当月及此前数据',true)+tableHTML(spec.title+'入选因子',selected,['role','name','transform','train_ic','block_stability','band_power','score','observations','max_selected_correlation'])+tableHTML(spec.title+'历史状态',rows,['month','state','confidence']));
    $('alloc-cycle-model').value=model;$('alloc-cycle-lookback').value=lookback;factors.forEach(function(item,index){allocFactorSignalChartV3(chartIds[index],data,item.factor,lookback);});allocCycleTimelineV3(timeline,data,model,lookback);
    $('alloc-cycle-model').onchange=function(){S.allocation.cycleModel=this.value;allocationCycle();};$('alloc-cycle-lookback').onchange=function(){S.allocation.lookback=this.value;allocationCycle();};
  }

  function allocRequiredStrategiesV3(chosen){return ['recommended','equal_weight'].concat(arr(chosen).filter(function(k){return !['recommended','equal_weight'].includes(k);})).slice(0,5);}
  function allocBacktestSeriesV3(data,key,windowName,mode){const rows=allocWindowRows(arr(obj(obj(obj(data.backtest).strategies)[key]).nav),windowName),field=mode==='gross'?'gross_nav':'nav',base=rows.length?Number(rows[0][field]):1;return {rows:rows,x:rows.map(function(r){return allocMonth(r.month)+'-01';}),nav:rows.map(function(r){return Number(r[field])/base;})};}
  function allocComparisonChartV3(id,data,strategies,windowName,mode){const ordered=allocRequiredStrategiesV3(strategies),traces=[],equal=allocBacktestSeriesV3(data,'equal_weight',windowName,mode),recommended=allocBacktestSeriesV3(data,'recommended',windowName,mode);traces.push({type:'scatter',mode:'lines',name:'等权基准',x:equal.x,y:equal.nav,line:{color:'#ed7d31',width:1.4},fill:'tozeroy',fillcolor:'rgba(237,125,49,.22)',hovertemplate:'%{x|%Y-%m}<br>等权 %{y:.3f}<extra></extra>'});traces.push({type:'scatter',mode:'lines',name:'推荐组合',x:recommended.x,y:recommended.nav,line:{color:'#c00000',width:3},hovertemplate:'%{x|%Y-%m}<br>推荐 %{y:.3f}<extra></extra>'});ordered.slice(2).forEach(function(key,index){const s=allocBacktestSeriesV3(data,key,windowName,mode);traces.push({type:'scatter',mode:'lines',name:ALLOC_STRATEGY_CN[key],x:s.x,y:s.nav,line:{color:['#2f75b5','#808080','#70ad47'][index%3],width:1.5},hovertemplate:'%{x|%Y-%m}<br>'+esc(ALLOC_STRATEGY_CN[key])+' %{y:.3f}<extra></extra>'});});const deco=allocSampleDecorations(recommended.rows);plot(id,traces,{height:430,hovermode:'x unified',yaxis:{title:mode==='gross'?'成本前净值':'成本后净值'},xaxis:{type:'date',rangeslider:allocRangeSlider()},shapes:deco.shapes,annotations:deco.annotations,legend:{orientation:'h',y:-.25},margin:{l:55,r:35,t:30,b:70}});}
  function allocActiveChartV3(id,data,windowName){const rows=allocWindowRows(arr(obj(obj(obj(data.backtest).strategies).recommended).nav),windowName),x=rows.map(function(r){return allocMonth(r.month)+'-01';}),active=rows.map(function(r){return Number(r.active_return)*100;});let relativeAcc=1;const relative=rows.map(function(r){relativeAcc*=(1+Number(r.active_return));return (relativeAcc-1)*100;}),deco=allocSampleDecorations(rows);plot(id,[{type:'bar',name:'月度主动收益',x:x,y:active,marker:{color:active.map(function(v){return v>=0?'rgba(192,0,0,.48)':'rgba(47,117,181,.42)';})},hovertemplate:'%{x|%Y-%m}<br>主动收益 %{y:.2f}%<extra></extra>'},{type:'scatter',mode:'lines',name:'累计超额',x:x,y:relative,yaxis:'y2',line:{color:'#666666',width:2.4},hovertemplate:'%{x|%Y-%m}<br>累计超额 %{y:.2f}%<extra></extra>'}],{height:320,hovermode:'x unified',barmode:'relative',yaxis:{title:'月度主动收益（%）'},yaxis2:{title:'累计超额（%）',overlaying:'y',side:'right',showgrid:false},xaxis:{type:'date'},shapes:deco.shapes,annotations:deco.annotations,legend:{orientation:'h',y:-.25},margin:{l:55,r:55,t:25,b:60}});}
  function allocDrawBacktest(navId,activeId,ddId,annualId,data,strategies,windowName,mode){const ordered=allocRequiredStrategiesV3(strategies);allocComparisonChartV3(navId,data,ordered,windowName,mode);allocActiveChartV3(activeId,data,windowName);const drawdowns=[],annualYears=new Set(),annualBy={};let decorationRows=[];ordered.forEach(function(key){const series=allocBacktestSeriesV3(data,key,windowName,mode);let peak=1,years={};if(!decorationRows.length)decorationRows=series.rows;drawdowns.push({type:'scatter',mode:'lines',name:ALLOC_STRATEGY_CN[key],x:series.x,y:series.nav.map(function(v){peak=Math.max(peak,v);return (v/peak-1)*100;}),line:{width:key==='recommended'?2.6:1.5,color:key==='recommended'?'#c00000':key==='equal_weight'?'#ed7d31':undefined}});series.rows.forEach(function(r,index){const field=mode==='gross'?'gross_nav':'nav',year=String(r.month).slice(0,4),value=Number(r[field]),prior=index?Number(series.rows[index-1][field]):Number(series.rows[0][field]),period=prior?value/prior-1:0;years[year]=(years[year]||1)*(1+period);annualYears.add(year);});annualBy[key]=years;});const deco=allocSampleDecorations(decorationRows);plot(ddId,drawdowns,{height:310,hovermode:'x unified',yaxis:{title:'回撤（%）'},xaxis:{type:'date'},shapes:deco.shapes,annotations:deco.annotations,legend:{orientation:'h',y:-.25}});const years=Array.from(annualYears).sort();plot(annualId,[{type:'heatmap',x:years,y:ordered.map(function(k){return ALLOC_STRATEGY_CN[k];}),z:ordered.map(function(k){return years.map(function(y){return ((annualBy[k][y]||1)-1)*100;});}),colorscale:[[0,'#2f75b5'],[.5,'#ffffff'],[1,'#c00000']],zmid:0,colorbar:{title:'%'},texttemplate:'%{z:.2f}',hovertemplate:'%{y}<br>%{x} %{z:.2f}%<extra></extra>'}],{height:310,xaxis:{showgrid:false},yaxis:{showgrid:false},margin:{l:105,r:45,t:20,b:45}});}
  function allocMetricRows(data,chosen){const rows=[];allocRequiredStrategiesV3(chosen).forEach(function(k){const strategy=obj(obj(obj(data.backtest).strategies)[k]),splits=obj(strategy.metrics_by_split),active=obj(strategy.active_metrics_by_split),all=[['full',obj(strategy.metrics),obj(strategy.active_metrics)],['train',obj(splits.train),obj(active.train)],['validation',obj(splits.validation),obj(active.validation)],['test',obj(splits.test),obj(active.test)]];all.forEach(function(item){const m=item[1],a=item[2];rows.push({strategy:ALLOC_STRATEGY_CN[k],sample_set:SAMPLE_CN[item[0]]||item[0],months:item[0]==='full'?arr(strategy.nav).length:Math.round(Number(m.months)||0),annual_return:allocPct(m.annual_return),annual_excess_return:allocPct(a.annual_excess_return),information_ratio:allocNumber(a.information_ratio),tracking_error:allocPct(a.tracking_error),active_month_hit_rate:allocPct(a.active_month_hit_rate),max_relative_drawdown:allocPct(a.max_relative_drawdown),sharpe:allocNumber(m.sharpe),max_drawdown:allocPct(m.max_drawdown),total_return:allocPct(m.total_return)});});});return rows;}

  async function allocationBacktest(){const data=await needAllocation(),chosen=allocRequiredStrategiesV3(S.allocation.backtest);header('回测检验','等权基准、训练验证测试与主动收益审计','资产配置');setText('as-of',allocMonth(obj(data.data_as_of).market));setText('generated-at',String(data.generated_at||'--').replace('T',' ').replace('Z',''));const config=obj(obj(data.backtest).config),audit=obj(data.optimization),gate=obj(audit.promotion_gate),testActive=obj(audit.test_active_metrics_report_only);conclusion('等权基准固定为四类ETF各25%、同样月度再平衡与交易成本；测试期推荐组合年化超额 '+allocPct(testActive.annual_excess_return)+'，信息比率 '+allocNumber(testActive.information_ratio)+'，最大相对回撤 '+allocPct(testActive.max_relative_drawdown)+'。模型状态：'+(gate.status==='passed'?'通过':'条件性输出')+'。');
    const c1=pid('abv'),c2=pid('aba'),c3=pid('abd'),c4=pid('aby');const controls='<section class="control-card"><div class="control-grid"><label style="grid-column:span 2;">对照策略（等权为固定基准）<select id="alloc-backtest-strategies" multiple size="7">'+Object.keys(ALLOC_STRATEGY_CN).map(function(k){const selected=chosen.includes(k),fixed=k==='equal_weight';return '<option value="'+k+'"'+(selected?' selected':'')+(fixed?' disabled':'')+'>'+ALLOC_STRATEGY_CN[k]+(fixed?'（固定）':'')+'</option>';}).join('')+'</select></label><label>观察窗口<select id="alloc-backtest-window"><option value="full">全部样本</option><option value="5y">近5年</option><option value="3y">近3年</option></select></label><label>净值口径<select id="alloc-nav-mode"><option value="net">成本后</option><option value="gross">成本前</option></select></label><button id="alloc-gpt-report" class="action-button preserve-acronym" type="button">GPT生成资产配置报告</button></div></section>';
    const leaderboard=arr(audit.leaderboard).map(function(row){return {strategy:row.id,family:row.family==='equity_preferred_dual_momentum'?'趋势增强':row.family,status:row.id===obj(audit.selected_spec).id?'最终入选':row.validation_eligible?'验证入围':(row.train_eligible||row.shortlisted_by_train)?'训练入围':'未入围',train_excess:allocPct(row.train_excess),validation_excess:allocPct(row.validation_excess),validation_information_ratio:allocNumber(row.validation_information_ratio),test_excess_report_only:allocPct(row.test_excess_report_only),turnover:allocPct(row.turnover)};});const costs=arr(obj(obj(data.backtest).robustness).cost_sensitivity_test).map(function(row){return {transaction_cost_bps:fmt(row.transaction_cost_bps,0),annual_return:allocPct(row.annual_return),annual_excess_return:allocPct(row.annual_excess_return),information_ratio:allocNumber(row.information_ratio),max_drawdown:allocPct(row.max_drawdown),max_relative_drawdown:allocPct(row.max_relative_drawdown)};});
    root(controls+'<div id="allocation-report" class="ai-panel is-compact preserve-acronym">'+(S.allocation.reportHtml||'')+'</div>'+panel(c1,'推荐组合与等权基准','橙色面积为等权；红线为推荐；训练 / 验证 / 测试已分区',true)+panel(c2,'累计超额与主动收益','灰线为累计超额，柱形为月度主动收益',true)+'<div class="panel-grid">'+panel(c3,'动态回撤','推荐、等权与所选策略')+panel(c4,'年度收益热力图','统一成本口径')+'</div>'+tableHTML('分样本回测与主动指标',allocMetricRows(data,chosen),['strategy','sample_set','months','annual_return','annual_excess_return','information_ratio','tracking_error','active_month_hit_rate','max_relative_drawdown','sharpe','max_drawdown','total_return'])+tableHTML('候选模型审计 · PBO '+allocPct(audit.pbo_cscv)+' / DSR '+allocPct(audit.deflated_sharpe_probability),leaderboard,['strategy','family','status','train_excess','validation_excess','validation_information_ratio','test_excess_report_only','turnover'])+tableHTML('测试集成本敏感性',costs,['transaction_cost_bps','annual_return','annual_excess_return','information_ratio','max_drawdown','max_relative_drawdown']));
    $('alloc-backtest-window').value=S.allocation.backtestWindow;$('alloc-nav-mode').value=S.allocation.navMode;allocDrawBacktest(c1,c2,c3,c4,data,chosen,S.allocation.backtestWindow,S.allocation.navMode);$('alloc-backtest-strategies').onchange=function(){const values=Array.from(this.selectedOptions).map(function(o){return o.value;});S.allocation.backtest=allocRequiredStrategiesV3(values);allocationBacktest();};$('alloc-backtest-window').onchange=function(){S.allocation.backtestWindow=this.value;allocationBacktest();};$('alloc-nav-mode').onchange=function(){S.allocation.navMode=this.value;allocationBacktest();};$('alloc-gpt-report').onclick=allocationGenerateReport;}
  /* r27: common-window data-quality repair */
  const R27_SW=['农林牧渔','基础化工','钢铁','有色金属','电子','家用电器','食品饮料','纺织服饰','轻工制造','医药生物','公用事业','交通运输','房地产','商贸零售','社会服务','综合','建筑材料','建筑装饰','电力设备','国防军工','计算机','传媒','通信','银行','非银金融','汽车','机械设备','煤炭','石油石化','环保','美容护理'];
  function r27Date(value){
    let text=String(value||'').trim();
    if(/^\d{6}$/.test(text)) text=text.slice(0,4)+'-'+text.slice(4,6)+'-01';
    if(/^\d{8}$/.test(text)) text=text.slice(0,4)+'-'+text.slice(4,6)+'-'+text.slice(6,8);
    const date=new Date(text.length===10?text+'T00:00:00Z':text);
    return Number.isFinite(date.getTime())?date:null;
  }
  function r27Clean(series){
    const map=new Map();
    arr(series&&((series.data||series.points))).forEach(function(point){
      const raw=point&&((point.date??point.time??point.as_of)),date=r27Date(raw),value=Number(point&&(point.value??point.close));
      if(date&&Number.isFinite(value)) map.set(date.toISOString().slice(0,10),{date:date,value:value});
    });
    return Array.from(map.values()).sort(function(a,b){return a.date-b.date;});
  }
  function r27Median(values){const x=values.filter(Number.isFinite).sort(function(a,b){return a-b;});return x.length?x[Math.floor(x.length/2)]:0;}
  function r27Tolerance(points){
    if(points.length<2)return 420;
    const gaps=[];for(let i=1;i<points.length;i++)gaps.push((points[i].date-points[i-1].date)/86400000);
    const median=r27Median(gaps);return median<=10?12:median<=45?62:median<=120?140:median<=220?240:420;
  }
  function r27MonthStart(date){return new Date(Date.UTC(date.getUTCFullYear(),date.getUTCMonth(),1));}
  function r27MonthEnd(date){return new Date(Date.UTC(date.getUTCFullYear(),date.getUTCMonth()+1,0));}
  function r27AlignedMonthly(list,opt){
    opt=opt||{};
    const prepared=arr(list).slice(0,6).map(function(series){return {series:series,points:r27Clean(series)};}).filter(function(item){return item.points.length>=2;});
    if(!prepared.length)return {items:[],x:[],start:'',end:''};
    let start=new Date(Math.max.apply(null,prepared.map(function(item){return item.points[0].date.getTime();})));
    let end=new Date(Math.min.apply(null,prepared.map(function(item){return item.points[item.points.length-1].date.getTime();})));
    const floor2010=new Date(Date.UTC(2010,0,1));if(start<floor2010)start=floor2010;
    if(opt.start&&r27Date(opt.start)&&start<r27Date(opt.start))start=r27Date(opt.start);
    start=r27MonthStart(start);end=r27MonthEnd(end);
    if(start>end)return {items:[],x:[],start:'',end:''};
    const grid=[];for(let cursor=new Date(start);cursor<=end;cursor=new Date(Date.UTC(cursor.getUTCFullYear(),cursor.getUTCMonth()+1,1)))grid.push(new Date(cursor));
    const indices=prepared.map(function(){return 0;}),tolerances=prepared.map(function(item){return r27Tolerance(item.points);}),rows=[];
    grid.forEach(function(month){
      const monthEnd=r27MonthEnd(month),values=[];let complete=true;
      prepared.forEach(function(item,index){
        while(indices[index]+1<item.points.length&&item.points[indices[index]+1].date<=monthEnd)indices[index]++;
        const point=item.points[indices[index]],age=(monthEnd-point.date)/86400000;
        if(!point||point.date>monthEnd||age<0||age>tolerances[index]){complete=false;values.push(null);}else values.push(point.value);
      });
      if(complete)rows.push({date:month.toISOString().slice(0,10),values:values});
    });
    const max=Number(opt.max)||0,selected=max&&rows.length>max?rows.slice(-max):rows;
    return {items:prepared.map(function(item,index){return {series:item.series,values:selected.map(function(row){return row.values[index];})};}),x:selected.map(function(row){return row.date;}),start:selected.length?selected[0].date:'',end:selected.length?selected[selected.length-1].date:''};
  }
  function r27AlignedDaily(list,opt){
    opt=opt||{};
    const prepared=arr(list).slice(0,6).map(function(series){return {series:series,points:r27Clean(series)};}).filter(function(item){return item.points.length>=2;});
    if(!prepared.length)return {items:[],x:[],start:'',end:''};
    let start=Math.max.apply(null,prepared.map(function(item){return item.points[0].date.getTime();})),end=Math.min.apply(null,prepared.map(function(item){return item.points[item.points.length-1].date.getTime();}));
    const keys=Array.from(new Set(prepared.flatMap(function(item){return item.points.map(function(point){return point.date.getTime();});}))).filter(function(value){return value>=start&&value<=end;}).sort(function(a,b){return a-b;});
    const indices=prepared.map(function(){return 0;}),rows=[];
    keys.forEach(function(timeValue){const at=new Date(timeValue),values=[];let complete=true;prepared.forEach(function(item,index){while(indices[index]+1<item.points.length&&item.points[indices[index]+1].date<=at)indices[index]++;const point=item.points[indices[index]],age=(at-point.date)/86400000;if(!point||point.date>at||age>5){complete=false;values.push(null);}else values.push(point.value);});if(complete)rows.push({date:at.toISOString().slice(0,10),values:values});});
    const max=Number(opt.max)||0,selected=max&&rows.length>max?rows.slice(-max):rows;
    return {items:prepared.map(function(item,index){return {series:item.series,values:selected.map(function(row){return row.values[index];})};}),x:selected.map(function(row){return row.date;}),start:selected.length?selected[0].date:'',end:selected.length?selected[selected.length-1].date:''};
  }
  function r27SpanMonths(x){if(!x.length)return 0;const a=r27Date(x[0]),b=r27Date(x[x.length-1]);return a&&b?(b-a)/2629800000:0;}
  function r27AxisSet(aligned,opt){
    const explicit=new Set(arr(opt&&opt.rightIds));if(explicit.size)return explicit;
    if(!aligned.items.length)return explicit;
    const first=aligned.items[0],baseUnit=String(first.series.unit||''),baseMedian=r27Median(first.values.map(Math.abs).filter(function(v){return v>0;}));
    aligned.items.slice(1).forEach(function(item){const median=r27Median(item.values.map(Math.abs).filter(function(v){return v>0;})),unit=String(item.series.unit||'');if((unit&&baseUnit&&unit!==baseUnit)||(baseMedian&&median&&(median/baseMedian>8||baseMedian/median>8)))explicit.add(item.series.id);});
    return explicit;
  }
  function r27LineLayout(x,hasRight,leftTitle,rightTitle){
    const long=r27SpanMonths(x)>=24,layout={hovermode:'x unified',legend:{orientation:'h',y:-.27,font:{size:12}},xaxis:{showgrid:false,type:'date',tickformat:'%Y年%m月',dtick:long?'M6':'M1',tickangle:0},yaxis:{title:leftTitle||'',gridcolor:'rgba(191,191,191,.55)',zerolinecolor:'#c8cdd2'},margin:{l:56,r:hasRight?58:24,t:14,b:72}};
    if(hasRight)layout.yaxis2={title:rightTitle||'',overlaying:'y',side:'right',showgrid:false,zeroline:false};return layout;
  }
  function lineSmart(id,list,opt){
    opt=Object.assign({max:0,rebase:false},opt||{});const aligned=opt.frequency==='daily'?r27AlignedDaily(list,opt):r27AlignedMonthly(list,opt),right=r27AxisSet(aligned,opt),leftUnits=[],rightUnits=[];
    const traces=aligned.items.map(function(item,index){const series=item.series,isRight=right.has(series.id),unit=cnText(series.unit||'');if(unit&&!isRight&&!leftUnits.includes(unit))leftUnits.push(unit);if(unit&&isRight&&!rightUnits.includes(unit))rightUnits.push(unit);let values=item.values.slice();if(opt.rebase&&values.length&&values[0])values=values.map(function(v){return v/values[0]*100;});return {type:'scatter',mode:'lines',connectgaps:false,name:seriesLabel(series)+(unit?' - '+unit:'')+(isRight?' - 右轴':''),x:aligned.x,y:values,yaxis:isRight?'y2':'y',line:{width:2.35,color:CHART_PALETTE[index%CHART_PALETTE.length]}};});
    plot(id,traces,r27LineLayout(aligned.x,right.size>0,opt.rebase?'基准=100':leftUnits.join('/'),rightUnits.join('/')));
  }
  function line(id,list,opt){
    opt=Object.assign({max:260,rebase:false},opt||{});const aligned=r27AlignedDaily(list,opt);const traces=aligned.items.map(function(item,index){let values=item.values.slice();if(opt.rebase&&values.length&&values[0])values=values.map(function(value){return value/values[0]*100;});return {type:'scatter',mode:'lines',connectgaps:false,name:seriesLabel(item.series),x:aligned.x,y:values,line:{width:2.25,color:CHART_PALETTE[index%CHART_PALETTE.length]}};});plot(id,traces,r27LineLayout(aligned.x,false,opt.rebase?'基准=100':'',''));
  }
  function r27CommonEnd(list){return r27AlignedMonthly(list,{max:0}).end||maxDate(list)||'--';}
  async function macro(){
    const cards=await fetchSeries(['cn_gdp_yoy','cn_cpi_yoy','cn_ppi_yoy','cn_m2_yoy']),top=cards.filter(function(series){return ['cn_gdp_yoy','cn_cpi_yoy','cn_m2_yoy'].includes(series.id);});
    conclusion('截至 '+r27CommonEnd(top)+'，'+top.map(trend).join('；')+'。');
    const specs=[
      {name:'增长与生产',a:['cn_gdp_yoy','cn_industrial_prod_yoy','cn_fai_yoy','cn_pmi_mfg'],al:['国内生产总值同比','工业增加值同比','固定资产投资同比','制造业采购经理指数'],ar:['cn_pmi_mfg'],b:['cn_pmi_mfg','cn_pmi_non_mfg','cn_lpi','cn_enterprise_boom'],bl:['制造业采购经理指数','非制造业采购经理指数','物流业景气指数','企业景气指数'],br:['cn_enterprise_boom']},
      {name:'需求与消费',a:['cn_retail_yoy','cn_retail_ytd_yoy','cn_mobile_shipments'],al:['社会消费品零售同比','社会消费品零售累计同比','手机出货量'],ar:['cn_mobile_shipments'],b:['cn_consumer_confidence','cn_consumer_satisfaction','cn_consumer_expectation'],bl:['消费者信心指数','消费者满意指数','消费者预期指数']},
      {name:'价格与通胀',a:['cn_cpi_yoy','cn_ppi_yoy','cn_cpi_mom'],al:['居民消费价格同比','工业生产者出厂价格同比','居民消费价格环比'],b:['cn_agri_wholesale_index','cn_commodity_price_index','cn_construction_material_index','cn_energy_index'],bl:['农产品批发价格指数','大宗商品价格指数','建材价格指数','能源价格指数'],br:['cn_agri_wholesale_index']},
      {name:'地产',a:['cn_new_house_yoy','cn_second_house_yoy'],al:['新房价格同比','二手房价格同比'],b:['cn_new_house_mom','cn_second_house_mom'],bl:['新房价格环比','二手房价格环比']},
      {name:'货币与流动性',a:['cn_m2_yoy','cn_m1_yoy','cn_m1_m2_gap'],al:['广义货币同比','狭义货币同比','狭义与广义货币增速差'],b:['cn_shibor_on','cn_shibor_1w','cn_shibor_1m','cn_lpr_1y'],bl:['上海银行间同业拆借隔夜利率','上海银行间同业拆借一周利率','上海银行间同业拆借一月利率','一年期贷款市场报价利率']},
      {name:'信用与财政',a:['cn_new_credit_month','cn_tsf_increment','cn_new_credit_ytd'],al:['新增人民币贷款','社会融资规模增量','新增贷款累计'],ar:['cn_new_credit_ytd'],b:['cn_fiscal_revenue_ytd_yoy','cn_tsf_rmb_loan'],bl:['财政收入累计同比','社会融资人民币贷款'],br:['cn_tsf_rmb_loan']},
      {name:'外贸与储备',a:['cn_export_yoy','cn_import_yoy','cn_trade_balance'],al:['出口同比','进口同比','贸易差额'],ar:['cn_trade_balance'],b:['cn_fx_reserves','cn_gold_reserves'],bl:['外汇储备','黄金储备'],br:['cn_gold_reserves']},
      {name:'运输与实体高频',a:['cn_electricity_secondary_yoy','cn_electricity_tertiary_yoy','cn_air_load_factor'],al:['第二产业用电同比','第三产业用电同比','民航客座率'],ar:['cn_air_load_factor'],b:['global_bdi','global_bci','global_bpi','global_bdti'],bl:['波罗的海干散货指数','好望角型船运指数','巴拿马型船运指数','成品油轮运输指数']}
    ];
    const ids=Array.from(new Set(specs.flatMap(function(spec){return spec.a.concat(spec.b);}))),map=idMap(await fetchSeries(ids)),html=[cardHTML(cards.map(function(series){return {series:series};}))],draw=[];
    specs.forEach(function(spec){const a=byIds(map,spec.a,spec.al),b=byIds(map,spec.b,spec.bl),p1=pid('m'),p2=pid('m'),asof=r27CommonEnd(a.concat(b));html.push('<div class="section-heading"><div><span class="eyebrow">宏观分项</span><h2>'+esc(spec.name)+'</h2><p>截至 '+esc(asof)+'，'+firstPointSeries(a.concat(b),2).map(trend).join('；')+'。</p></div></div><div class="panel-grid">'+panel(p1,spec.name+' 第一组指标','',false)+panel(p2,spec.name+' 第二组指标','',false)+'</div>');draw.push(function(){lineSmart(p1,a,{rightIds:spec.ar||[]});},function(){lineSmart(p2,b,{rightIds:spec.br||[]});});});root(html.join(''));draw.forEach(function(fn){fn();});
  }
  let R35_INDUSTRY_CACHE=new Map();
  async function r35IndustryBundle(names){
    const key=names.slice().sort().join('|');
    if(!R35_INDUSTRY_CACHE.has(key)){
      const url='/api/rotation/industry-dashboard?industries='+encodeURIComponent(names.join(','));
      R35_INDUSTRY_CACHE.set(key,api(url).catch(function(error){R35_INDUSTRY_CACHE.delete(key);throw error;}));
    }
    return R35_INDUSTRY_CACHE.get(key);
  }
  function r35ScoreSeries(row){
    const history=arr(row&&row.score_history),definitions=[['score','行业景气评分'],['growth','增长分项'],['quality','质量分项'],['m250x20','中期动量分项'],['anti_crowding','反拥挤分项']];
    return definitions.map(function(def){return {id:'r35_score_'+def[0]+'_'+row.industry,name:def[1],unit:'分',data:history.map(function(item){const value=def[0]==='score'?item.score:obj(item.components)[def[0]];return {date:item.date,value:value};}).filter(function(item){return item.date&&Number.isFinite(Number(item.value));})};}).filter(function(series){return series.data.length>=2;});
  }
  function r35HighFrequency(row){
    const live=arr(row&&row.indicators).filter(function(item){return item.status==='live'&&arr(item.data).length>=2;}),units=[],selected=[];
    live.forEach(function(item){const unit=String(item.unit||'');if(!units.includes(unit)&&units.length>=2)return;if(!units.includes(unit))units.push(unit);if(selected.length<4)selected.push(item);});
    return selected.map(function(item,index){return {id:'r35_hf_'+String(item.variable||item.series_id||index)+'_'+row.industry,name:cnText(item.name||item.field||'产业指标'),unit:cnText(item.unit||''),frequency:cnText(item.frequency||''),as_of:item.last_date,data:arr(item.data)};});
  }
  function r35MarketSeries(row){
    const history=arr(row&&row.trend),definitions=[['industry','行业指数'],['equal_weight','申万一级行业等权'],['relative','行业相对强弱']];
    return definitions.map(function(def){return {id:'r35_market_'+def[0]+'_'+row.industry,name:def[1],unit:'指数',frequency:'日',data:history.map(function(item){return {date:item.date,value:item[def[0]]};}).filter(function(item){return item.date&&Number.isFinite(Number(item.value));})};}).filter(function(series){return series.data.length>=2;});
  }
  function r35PrimarySeries(row){
    const high=r35HighFrequency(row),score=r35ScoreSeries(row);
    if(high.length===0)return {series:score,title:row.industry+' 景气评分跟踪',subtitle:'暂无可持续实取产业字段；展示本行业评分，不作产业数据替代',frequency:'monthly'};
    if(high.length===1&&score.length)return {series:high.concat(score.slice(0,1)),title:row.industry+' 产业指标与景气评分',subtitle:'实取产业指标：'+high[0].name+'；辅以本行业景气评分',frequency:'monthly'};
    const daily=high.every(function(item){return item.frequency==='日';});
    return {series:high,title:row.industry+' 产业高频监测',subtitle:'实取产业指标：'+high.map(function(item){return item.name;}).join('、'),frequency:daily?'daily':'monthly'};
  }
  async function sw(){
    const raw=arr(table('sw_industries','sw_l1_full_snapshot').rows),index=new Map(raw.map(function(row){return [row.industry,row];})),rows=R27_SW.map(function(name){return index.get(name);}).filter(Boolean);
    if(!S.sw)S.sw=['电子','通信','计算机','电力设备','医药生物','有色金属','银行','食品饮料'];
    const selected=rows.filter(function(row){return S.sw.includes(row.industry);}),names=selected.map(function(row){return row.industry;}),bundle=names.length?await r35IndustryBundle(names):{industries:[],as_of:maxDate(rows)},bundleMap=new Map(arr(bundle.industries).map(function(row){return [row.industry,row];})),asof=maxDate(selected.length?selected:rows),liveCount=arr(bundle.industries).reduce(function(sum,row){return sum+Number(row.live_indicators||0);},0),totalCount=arr(bundle.industries).reduce(function(sum,row){return sum+Number(row.total_indicators||0);},0);
    conclusion('<span>截至 '+esc(asof)+'，最近一日核心行业均值 '+signed(avg(selected,'ret_1d'))+'%，最近一周核心行业均值 '+signed(avg(selected,'ret_5d'))+'%。</span>');
    const html=[control('sw-select',TXT.swPick,rows,'industry',S.sw,'sw-apply','sw-reset'),cardHTML([{label:'已选行业',value:selected.length,as_of:asof},{label:'申万一级行业',value:rows.length},{label:'实取产业字段',value:liveCount,unit:'个'},{label:'已核验字段',value:totalCount,unit:'个'}])],draw=[];
    selected.forEach(function(marketRow){
      const row=bundleMap.get(marketRow.industry)||{industry:marketRow.industry,indicators:[],trend:[],score_history:[],live_indicators:0,total_indicators:0},primary=r35PrimarySeries(row),market=r35MarketSeries(row),p1=pid('sw'),p2=pid('sw'),missing=arr(row.indicators).filter(function(item){return item.status!=='live';}).map(function(item){return item.name;});
      html.push('<div class="section-heading"><div><span class="eyebrow">行业景气</span><h2>'+esc(marketRow.industry)+'</h2><p>截至 '+esc(marketRow.as_of||asof)+'，一日 '+signed(marketRow.ret_1d)+'%，一周 '+signed(marketRow.ret_5d)+'%，二十日 '+signed(marketRow.ret_20d)+'%。</p></div></div><div class="panel-grid">'+panel(p1,primary.title,primary.subtitle,false)+panel(p2,marketRow.industry+' 行情与相对强弱','行业指数、申万一级行业等权与本行业相对强弱',false)+'</div>');
      draw.push(function(){lineSmart(p1,primary.series,{frequency:primary.frequency==='daily'?'daily':undefined,max:520});},function(){lineSmart(p2,market,{frequency:'daily',max:520});});
    });
    html.push(tableHTML('申万一级行业全量',rows,['code','industry','close','ret_1d','ret_5d','ret_20d','vol_20d','mdd_60d','as_of','source']));
    root(html.join(''));
    $('sw-apply').onclick=async function(){S.sw=Array.from($('sw-select').selectedOptions).map(function(option){return option.value;});if(!S.sw.length)S.sw=null;await sw();};
    $('sw-reset').onclick=async function(){S.sw=null;await sw();};
    draw.forEach(function(fn){fn();});
  }  let R27_SINA=null,R27_LHB=null;
  async function r27Sina(){if(!R27_SINA){try{R27_SINA=await api('/api/news/sina24h?limit=200');}catch(_){R27_SINA={rows:[]};}}return R27_SINA;}
  async function r27Lhb(){if(!R27_LHB){try{R27_LHB=await api('/api/market/lhb');}catch(_){R27_LHB={industries:[],stocks:[]};}}return R27_LHB;}
  function r27MergeNews(){const map=new Map();Array.from(arguments).forEach(function(rows){arr(rows).forEach(function(row){const key=String(row.id||'')||String(row.published_at||'')+'|'+String(row.title||'');if(!map.has(key))map.set(key,row);});});return Array.from(map.values()).sort(function(a,b){return String(b.published_at||'').localeCompare(String(a.published_at||''));});}
  function r27Week(rows){const latest=rows[0]&&r27Date(rows[0].published_at);if(!latest)return rows;const cutoff=new Date(latest.getTime()-7*86400000);return rows.filter(function(row){const date=r27Date(row.published_at);return date&&date>=cutoff;});}
  function r27AutoScroll(element){if(!element||element.dataset.autoScroll==='1')return;element.dataset.autoScroll='1';let paused=false,hold=0;const pause=function(){paused=true;},resume=function(){paused=false;};element.addEventListener('mouseenter',pause);element.addEventListener('mouseleave',resume);element.addEventListener('focusin',pause);element.addEventListener('focusout',resume);element.addEventListener('touchstart',pause,{passive:true});element.addEventListener('touchend',resume,{passive:true});element.addEventListener('wheel',function(){pause();clearTimeout(element._resumeTimer);element._resumeTimer=setTimeout(resume,2200);},{passive:true});element._autoTimer=setInterval(function(){if(paused||element.scrollHeight<=element.clientHeight+2)return;if(element.scrollTop+element.clientHeight>=element.scrollHeight-2){hold++;if(hold>30){element.scrollTop=0;hold=0;}}else{element.scrollTop+=1;hold=0;}},180);}
  function r27RankBar(id,rows,title){
    const data=arr(rows).filter(function(row){return Number.isFinite(Number(row.value));}).slice().sort(function(a,b){return Number(a.value)-Number(b.value);}),height=Math.max(430,130+data.length*29),maximum=Math.max(1,...data.map(function(row){return Number(row.value)||0;}));
    plot(id,data.length?[{type:'bar',orientation:'h',x:data.map(function(row){return Number(row.value);}),y:data.map(function(row){return cnText(row.label);}),customdata:data.map(function(row){return [row.count||0,row.net_buy_wan||0,row.turnover_wan||0];}),text:data.map(function(row){return fmt(row.value,2);}),textposition:data.map(function(row){return Number(row.value)>8?'inside':'outside';}),cliponaxis:false,marker:{color:data.map(function(row){return Number(row.value)>0?'#c00000':'#bfc5cc';})},hovertemplate:'%{y}<br>'+title+' %{x:.2f}<br>\u4e0a\u699c\u6b21\u6570 %{customdata[0]}<br>\u51c0\u4e70\u989d %{customdata[1]:,.2f} \u4e07\u5143<br>\u6210\u4ea4\u989d %{customdata[2]:,.2f} \u4e07\u5143<extra></extra>'}]:[],{height:height,showlegend:false,xaxis:{title:title,gridcolor:'rgba(191,191,191,.55)',range:[0,maximum*1.13],rangemode:'tozero'},yaxis:{automargin:true,tickfont:{size:12}},margin:{l:data.length>15?118:140,r:60,t:14,b:58}});
  }
  async function news(){
    const results=await Promise.allSettled([r27Sina(),r27Lhb()]),sina=results[0].status==='fulfilled'?results[0].value:{rows:[]},lhb=results[1].status==='fulfilled'?results[1].value:{industries:[],stocks:[]},base=arr(table('news_events','news_feed').rows),rows=r27MergeNews(arr(sina.rows),base),week=r27Week(rows),latest=rows[0]||{},p1=pid('n'),p2=pid('n');
    conclusion('截至 '+esc(latest.published_at||'--')+'，最新事件：“'+esc(latest.title||'暂无标题')+'”；最近一周收录 '+week.length+' 条。');
    const industries=arr(lhb.industries).map(function(row){return {label:row.industry,value:row.heat_score,count:row.count,net_buy_wan:row.net_buy_wan,turnover_wan:row.turnover_wan};}),stocks=arr(lhb.stocks).map(function(row){return {label:(row.name||row.code)+'\uff08'+row.code+'\uff09',value:row.heat_score,count:row.count,net_buy_wan:row.net_buy_wan,turnover_wan:row.turnover_wan};}),activeIndustries=industries.filter(function(row){return Number(row.count)>0;}).length;
    root(cardHTML([{label:'\u8fd1\u4e00\u5468\u65b0\u95fb',value:week.length,as_of:String(latest.published_at||'').slice(0,10)},{label:'\u7533\u4e07\u4e00\u7ea7\u884c\u4e1a',value:industries.length,unit:'\u4e2a'},{label:'\u6709\u4e0a\u699c\u884c\u4e1a',value:activeIndustries,unit:'\u4e2a'},{label:'\u9f99\u864e\u699c\u4e2a\u80a1',value:stocks.length,unit:'\u53ea'}])+'<section class="chart-panel wide"><div class="panel-header"><div><h3>\u65b0\u95fb\u6eda\u52a8</h3></div></div><div class="news-ticker" id="news-r27"><div class="news-list">'+(week.length?week:rows).map(newsItem).join('')+'</div></div></section><div class="rank-grid">'+panel(p1,'\u7533\u4e07\u4e00\u7ea7\u884c\u4e1a\u9f99\u864e\u699c\u70ed\u5ea6\u6392\u540d','',true)+panel(p2,'\u4e2a\u80a1\u9f99\u864e\u699c\u70ed\u5ea6\u524d\u5341','',true)+'</div>'+tableHTML('\u65b0\u95fb\u660e\u7ec6',week.length?week:rows,['published_at','event_type','code','title','source','url']));
    r27RankBar(p1,industries,'\u9f99\u864e\u699c\u70ed\u5ea6\u5206');r27RankBar(p2,stocks,'\u9f99\u864e\u699c\u70ed\u5ea6\u5206');r27AutoScroll($('news-r27'));
  }
  async function drawStockKline(id,code){
    try{
      const payload=await api('/api/stock/ohlc/'+encodeURIComponent(code)+'?limit=320'),seen=new Map();
      arr(payload.rows).forEach(function(row){const date=String(row.date||'').slice(0,10),open=Number(row.open),high=Number(row.high),low=Number(row.low),close=Number(row.close),volume=Number(row.volume);if(date&&[open,high,low,close].every(Number.isFinite))seen.set(date,{date:date,open:open,high:high,low:low,close:close,volume:Number.isFinite(volume)?volume:0});});
      const rows=Array.from(seen.values()).sort(function(a,b){return a.date.localeCompare(b.date);}),x=rows.map(function(row){return row.date;}),close=rows.map(function(row){return row.close;});
      function movingAverage(windowSize){return close.map(function(_,index){if(index+1<windowSize)return null;let sum=0;for(let offset=index-windowSize+1;offset<=index;offset++)sum+=close[offset];return sum/windowSize;});}
      const ticks=[],tickText=[],tickIndexes=[],months=new Set();x.forEach(function(date,index){const month=date.slice(0,7);if(!months.has(month)){months.add(month);if(tickIndexes.length&&index-tickIndexes[tickIndexes.length-1]<12){ticks[ticks.length-1]=date;tickText[tickText.length-1]=month;tickIndexes[tickIndexes.length-1]=index;}else{ticks.push(date);tickText.push(month);tickIndexes.push(index);}}});
      const traces=rows.length?[{type:'candlestick',x:x,open:rows.map(function(row){return row.open;}),high:rows.map(function(row){return row.high;}),low:rows.map(function(row){return row.low;}),close:close,name:'\u65e5K\u7ebf',increasing:{line:{color:'#c00000'},fillcolor:'#c00000'},decreasing:{line:{color:'#00b050'},fillcolor:'#00b050'}},{type:'scatter',mode:'lines',x:x,y:movingAverage(5),name:'5\u65e5\u5747\u7ebf',connectgaps:true,line:{color:'#ffc000',width:1.6}},{type:'scatter',mode:'lines',x:x,y:movingAverage(20),name:'20\u65e5\u5747\u7ebf',connectgaps:true,line:{color:'#2f75b5',width:1.6}},{type:'scatter',mode:'lines',x:x,y:movingAverage(60),name:'60\u65e5\u5747\u7ebf',connectgaps:true,line:{color:'#7030a0',width:1.6}},{type:'bar',x:x,y:rows.map(function(row){return row.volume;}),name:'\u6210\u4ea4\u91cf',yaxis:'y2',marker:{color:'rgba(47,117,181,.22)'}}]:[];
      plot(id,traces,{height:610,xaxis:{rangeslider:{visible:false},showgrid:false,type:'category',categoryorder:'array',categoryarray:x,tickmode:'array',tickvals:ticks,ticktext:tickText},yaxis:{domain:[.24,1],gridcolor:'rgba(191,191,191,.55)'},yaxis2:{domain:[0,.15],showgrid:false},legend:{orientation:'h',y:-.2},hovermode:'x unified',margin:{l:52,r:24,t:14,b:76}});
    }catch(error){const element=$(id);if(element)element.innerHTML='<div class="chart-fallback">K\u7ebf\u52a0\u8f7d\u5931\u8d25\uff1a'+esc(error.message)+'</div>';}
  }
  async function stock(){
    const base=S.stockOverride||mod('stock'),watch=arr(table('stock','stock_watchlist').rows);if(!S.stockCode)S.stockCode=(watch[0]&&watch[0].code)||'000001';const baseRows=arr((arr(base.tables).find(function(item){return item.id==='stock_watchlist';})||{}).rows||watch),record=obj(base.record||{}),selected=digits(S.stockCode),quote=record.code?record:(baseRows.find(function(row){return digits(row.code)===selected;})||watch.find(function(row){return digits(row.code)===selected;})||baseRows[0]||watch[0]||{}),sina=await r27Sina(),allNews=r27MergeNews(arr(sina.rows),arr(table('news_events','news_feed').rows)),stockNews=relatedNews(allNews,quote.code||S.stockCode,quote.name),asof=quote.as_of||maxDate(baseRows.length?baseRows:watch),p1=pid('s'),p2=pid('s');
    conclusion('截至 '+asof+'，'+esc(quote.name||S.stockCode)+' 日涨跌 '+signed(quote.ret_1d)+'%，周涨跌 '+signed(quote.ret_5d)+'%，二十日涨跌 '+signed(quote.ret_20d)+'%，二十日回撤 '+signed(quote.mdd_20d)+'%；相关新闻 '+stockNews.length+' 条。');
    root('<section class="control-card"><div class="control-grid"><label>'+TXT.stockPick+'<select id="stock-preset">'+watch.map(function(row){return '<option value="'+esc(row.code)+'" '+(digits(row.code)===selected?'selected':'')+'>'+esc(cnText(row.code))+' '+esc(row.name)+'</option>';}).join('')+'</select></label><label style="grid-column:span 2;">'+TXT.inputCode+'<input id="stock-input" value="'+esc(S.stockCode)+'"></label><button id="stock-load" class="action-button" type="button">'+TXT.loadStock+'</button><div class="ai-actions"><button id="stock-ai" class="ghost-button" type="button">智能分析</button><button id="stock-deep" class="ghost-button" type="button">深度报告</button></div></div></section>'+cardHTML([{label:'收盘价',value:quote.close??quote.qfq_close,unit:'元',as_of:asof},{label:'一周涨跌',value:quote.ret_5d,unit:'%'},{label:'二十日涨跌',value:quote.ret_20d,unit:'%'},{label:'二十日回撤',value:quote.mdd_20d,unit:'%'}])+'<div class="ai-dual-grid"><section><h3>智能分析</h3><div id="stock-ai-result" class="ai-panel is-compact"><p>点击“智能分析”生成投资建议。</p></div></section><section><h3>深度报告</h3><div id="stock-deep-result" class="ai-panel is-compact"><p>点击“深度报告”生成六段框架报告。</p></div></section></div><div class="panel-grid">'+panel(p1,'个股K线行情','',true)+panel(p2,'自选股风险收益','',false)+'</div><section class="chart-panel wide"><div class="panel-header"><div><h3>个股新闻滚动</h3></div></div><div class="news-ticker stock-news" id="stock-news-r27"><div class="news-list">'+(stockNews.length?stockNews:allNews.slice(0,20)).map(newsItem).join('')+'</div></div></section>'+tableHTML('个股行情',baseRows.length?baseRows:watch,['code','name','close','qfq_close','ret_1d','ret_5d','ret_20d','vol_20d','mdd_20d','turnover','as_of'])+tableHTML('相关个股新闻',stockNews,['published_at','event_type','code','title','source','url']));
    $('stock-preset').onchange=function(){$('stock-input').value=$('stock-preset').value;};$('stock-load').onclick=async function(){const code=$('stock-input').value.trim();if(!code)return;S.stockCode=code;try{const payload=await api('/api/board/stock/'+encodeURIComponent(code));S.stockOverride=payload.data||payload;}catch(error){S.stockOverride=null;conclusion('个股加载失败：'+esc(error.message));}await stock();};const context={quote:quote,news:stockNews.slice(0,20),watchlist:watch.slice(0,20)};$('stock-ai').onclick=function(){return aiFillMode('stock',(quote.code||S.stockCode)+' '+(quote.name||''),context,'stock-ai-result','analysis');};$('stock-deep').onclick=function(){return aiFillMode('stock',(quote.code||S.stockCode)+' '+(quote.name||''),context,'stock-deep-result','deep_report');};drawStockKline(p1,quote.code||S.stockCode);scatter(p2,watch.map(function(row){return {label:row.name||row.code,x:row.vol_20d,y:row.ret_20d};}));r27AutoScroll($('stock-news-r27'));
  }

  /* r43 portfolio display: plain labels, grouped controls, dense visual evidence. */
  Object.assign(S.portfolio,{poolWindow:S.portfolio.poolWindow||'3y',curveCount:S.portfolio.curveCount||6,assetCodes:S.portfolio.assetCodes||{},topN:S.portfolio.topN||20});
  function poHeading(title){return '<div class="section-heading portfolio-subhead"><div><h2>'+esc(title)+'</h2></div></div>';}
  function poPlanName(row){row=obj(row);const e={shrink_momentum:'收缩动量',robust_bl:'稳健BL',risk_adjusted_trend:'风险调整趋势'}[row.expected_return_method]||row.expected_return_method||'收益观点',c={lw:'收缩协方差',ewma:'指数加权协方差'}[row.covariance_method]||row.covariance_method||'风险模型';return e+' · '+c+' · '+Math.round(Number(row.lookback_days)||0)+'日';}
  function poInsertHeading(node,title){if(node)node.insertAdjacentHTML('beforebegin',poHeading(title));}
  function poCurrentGroups(data){const map={};arr(obj(data.home).current_weights).forEach(function(r){const key=PO_GROUP_CN[r.group]||r.group;map[key]=(map[key]||0)+Number(r.weight||0);});return Object.entries(map).map(function(x){return {label:x[0],value:x[1]};});}
  async function portfolioHomeR43(){
    await portfolioHome();const data=await needPortfolio(),pane=document.querySelector('.view-cache-pane[data-view="'+S.active+'"]');if(!pane)return;const grids=pane.querySelectorAll('.panel-grid'),tables=pane.querySelectorAll('.table-panel');poInsertHeading(pane.querySelector('.kpi-grid'),'核心指标');poInsertHeading(grids[0],'当前组合');poInsertHeading(tables[0],'流程状态');if(tables[1])poInsertHeading(tables[1],'回测结论');if(tables[3])poInsertHeading(tables[3],'权重明细');$('core-conclusion').classList.add('portfolio-conclusion');
    const a=pid('ph'),b=pid('ph'),c=pid('ph'),d=pid('ph'),html=poHeading('模型状态')+'<div class="panel-grid">'+panel(a,'风险袖套权重','')+panel(b,'分样本收益与夏普','')+panel(c,'求解速度与残差','')+panel(d,'晋级门槛','')+'</div>';grids[0].insertAdjacentHTML('afterend',html);
    const groups=poCurrentGroups(data);plot(a,[{type:'bar',x:groups.map(function(r){return r.label;}),y:groups.map(function(r){return r.value*100;}),marker:{color:['#2f75b5','#b42318','#c46a08','#168a47','#7030a0']},text:groups.map(function(r){return poPct(r.value);}),textposition:'auto'}],{height:340,showlegend:false,yaxis:{title:'权重（%）'},xaxis:{showgrid:false}});
    const splits=['train','validation','test'],metrics=obj(obj(obj(data.backtest).strategies).selected).metrics;plot(b,[{type:'bar',name:'年化收益',x:splits.map(function(k){return PO_SAMPLE_CN[k];}),y:splits.map(function(k){return Number(obj(metrics[k]).annual_return)*100;}),marker:{color:'#b42318'}},{type:'scatter',mode:'lines+markers',name:'夏普',x:splits.map(function(k){return PO_SAMPLE_CN[k];}),y:splits.map(function(k){return Number(obj(metrics[k]).sharpe);}),yaxis:'y2',line:{color:'#2f75b5',width:2.4}}],{height:340,yaxis:{title:'年化收益（%）'},yaxis2:{title:'夏普',overlaying:'y',side:'right'},legend:{orientation:'h',y:-.2}});
    const sol=arr(obj(data.optimization).solver_benchmark);plot(c,[{type:'scatter',mode:'markers+text',x:sol.map(function(r){return Number(r.median_ms);}),y:sol.map(function(r){return r.max_constraint_violation==null?null:Math.max(Number(r.max_constraint_violation),1e-12);}),text:sol.map(function(r){return r.solver;}),textposition:'top center',marker:{size:14,color:sol.map(function(r){return r.status==='optimal'?'#168a47':'#b42318';})},hovertemplate:'%{text}<br>%{x:.3f}ms<br>残差 %{y:.2e}<extra></extra>'}],{height:340,xaxis:{title:'中位耗时（ms）'},yaxis:{title:'最大残差',type:'log'}});
    const gate=obj(obj(data.backtest).promotion_gate),pbo=Number(obj(obj(data.optimization).pbo_cscv).pbo),dsr=Number(obj(data.optimization).deflated_sharpe_probability);plot(d,[{type:'bar',x:['PBO通过度','DSR','影子运行'],y:[Math.max(0,1-pbo/.2)*100,dsr*100,0],marker:{color:['#c46a08','#2f75b5','#98a2b3']},text:[poPct(pbo),poPct(dsr),'0/'+gate.shadow_months_required+'月'],textposition:'auto'}],{height:340,yaxis:{title:'门槛完成度（%）',range:[0,105]},xaxis:{showgrid:false}});
  }
  function poCurveRows(series,windowName){const n=windowName==='1y'?252:windowName==='2y'?504:756;return arr(series.data).slice(-n);}
  function poDrawPoolAdvanced(navId,data,type){const all=arr(obj(obj(data.asset_pool).nav_series)[type]),saved=arr(obj(S.portfolio.assetCodes)[type]),codes=saved.length?saved:all.slice(0,S.portfolio.curveCount).map(function(r){return r.code;}),chosen=all.filter(function(r){return codes.includes(r.code);}).slice(0,10);plot(navId,chosen.map(function(series,index){const points=poCurveRows(series,S.portfolio.poolWindow),base=points.length?Number(points[0].value):1;return {type:'scatter',mode:'lines',name:series.name||series.code,x:points.map(function(p){return poDate(p.date);}),y:points.map(function(p){return Number(p.value)/base;}),line:{width:2,color:index===0?PO_TYPE_COLORS[type]:CHART_PALETTE[index%CHART_PALETTE.length]}};}),{height:410,hovermode:'x unified',xaxis:{type:'date',rangeslider:{visible:true,thickness:.06}},yaxis:{title:'区间归一净值'},legend:{orientation:'h',y:-.28}});}
  async function portfolioPoolR43(){
    await portfolioPool();const data=await needPortfolio(),type=S.portfolio.assetType,pane=document.querySelector('.view-cache-pane[data-view="'+S.active+'"]');if(!pane)return;const grid=pane.querySelector('.panel-grid'),table=pane.querySelector('.table-panel'),controlGrid=pane.querySelector('.control-grid'),all=arr(obj(obj(data.asset_pool).nav_series)[type]),saved=arr(obj(S.portfolio.assetCodes)[type]);poInsertHeading(pane.querySelector('.control-card'),'查看条件');poInsertHeading(grid,'收益表现');poInsertHeading(table,'资产明细');$('core-conclusion').classList.add('portfolio-conclusion');
    controlGrid.insertAdjacentHTML('beforeend','<label>观察窗口<select id="po-pool-window"><option value="3y">近3年</option><option value="2y">近2年</option><option value="1y">近1年</option></select></label><label style="grid-column:span 2;">净值标的<select id="po-asset-codes" multiple size="5">'+all.map(function(r,index){const chosen=saved.length?saved.includes(r.code):index<S.portfolio.curveCount;return '<option value="'+esc(r.code)+'"'+(chosen?' selected':'')+'>'+esc(r.name||r.code)+'</option>';}).join('')+'</select></label>');$('po-pool-window').value=S.portfolio.poolWindow;
    const frames=pane.querySelectorAll('.plot-frame');if(frames[0])poDrawPoolAdvanced(frames[0].id,data,type);const a=pid('ppx'),b=pid('ppx'),c=pid('ppx'),d=pid('ppx');grid.insertAdjacentHTML('afterend',poHeading('风险与交易特征')+'<div class="panel-grid">'+panel(a,'区间回撤','')+panel(b,'日收益分布','')+panel(c,'收益风险排序','')+panel(d,'流动性与尾部风险','')+'</div>');
    const profiles=arr(obj(data.asset_pool).profiles).filter(function(r){return r.asset_type===type;}),chosenSeries=all.filter(function(r){return (saved.length?saved:all.slice(0,S.portfolio.curveCount).map(function(x){return x.code;})).includes(r.code);}).slice(0,8);plot(a,chosenSeries.map(function(series){const rows=poCurveRows(series,S.portfolio.poolWindow),values=rows.map(function(r){return Number(r.value);}),peaks=[];let peak=0;values.forEach(function(v){peak=Math.max(peak,v);peaks.push(peak);});return {type:'scatter',mode:'lines',name:series.name||series.code,x:rows.map(function(r){return poDate(r.date);}),y:values.map(function(v,i){return (v/Math.max(peaks[i],1e-12)-1)*100;})};}),{height:360,hovermode:'x unified',yaxis:{title:'回撤（%）'},legend:{orientation:'h',y:-.25}});
    const distributions=chosenSeries.slice(0,4).map(function(series){const rows=poCurveRows(series,'1y'),vals=[];for(let i=1;i<rows.length;i++)vals.push(Number(rows[i].value)/Number(rows[i-1].value)-1);return {type:'box',name:series.name||series.code,y:vals.map(function(v){return v*100;}),boxpoints:false};});plot(b,distributions,{height:360,yaxis:{title:'日收益（%）'},showlegend:false});
    const ranked=profiles.slice().sort(function(x,y){return Number(y.sharpe_1y)-Number(x.sharpe_1y);}).slice(0,12);plot(c,[{type:'bar',orientation:'h',x:ranked.map(function(r){return Number(r.sharpe_1y);}),y:ranked.map(function(r){return r.name||r.code;}),marker:{color:ranked.map(function(r){return Number(r.sharpe_1y)>=0?'#b42318':'#168a47';})},text:ranked.map(function(r){return poNum(r.sharpe_1y,2);}),textposition:'auto'}],{height:360,xaxis:{title:'近一年夏普'},yaxis:{automargin:true,autorange:'reversed'}});
    plot(d,[{type:'scatter',mode:'markers+text',x:profiles.map(function(r){return Math.log10(Math.max(Number(r.average_amount)||1,1));}),y:profiles.map(function(r){return Number(r.daily_cvar_95)*100;}),text:profiles.map(function(r){return r.name||r.code;}),textposition:'top center',marker:{size:11,color:PO_TYPE_COLORS[type],opacity:.72},hovertemplate:'%{text}<br>成交额log %{x:.2f}<br>CVaR %{y:.2f}%<extra></extra>'}],{height:360,xaxis:{title:'日均成交额（对数）'},yaxis:{title:'日度CVaR 95%（%）'}});
    $('po-asset-type').onchange=function(){S.portfolio.assetType=this.value;portfolioPoolR43();};$('po-pool-window').onchange=function(){S.portfolio.poolWindow=this.value;portfolioPoolR43();};$('po-asset-codes').onchange=function(){S.portfolio.assetCodes[type]=Array.from(this.selectedOptions).map(function(o){return o.value;}).slice(0,10);portfolioPoolR43();};
  }
  async function portfolioRiskR43(){
    await portfolioRisk();const data=await needPortfolio(),risk=obj(data.risk_constraints),pane=document.querySelector('.view-cache-pane[data-view="'+S.active+'"]');if(!pane)return;const grid=pane.querySelector('.panel-grid'),tables=Array.from(pane.querySelectorAll('.table-panel')),parameters=arr(risk.parameters),shown=S.portfolio.parameterGroup==='全部'?parameters:parameters.filter(function(r){return r.group===S.portfolio.parameterGroup;});poInsertHeading(pane.querySelector('.control-card'),'查看条件');poInsertHeading(grid,'风险估计');if(tables[0])poInsertHeading(tables[0],'模型与约束');$('core-conclusion').classList.add('portfolio-conclusion');
    const oldParam=tables[tables.length-1];if(oldParam){const priority=['lookback','covariance','risk_aversion','position','turnover','transaction','confidence','shrink','solver','tolerance','embargo','cost','cvar','volatility'];const important=parameters.filter(function(r){return r.tunable||priority.some(function(k){return String(r.parameter).toLowerCase().includes(k);});}).slice(0,28);oldParam.insertAdjacentHTML('beforebegin',poHeading('关键参数')+poTableHTML('当前关键参数',important,['group','parameter','value','status','tunable'])+'<details class="portfolio-details"><summary>查看分类参数（'+shown.length+'）</summary>'+poTableHTML('参数表',shown,['group','parameter','value','status','tunable'])+'</details>');oldParam.remove();}
    const a=pid('prx'),b=pid('prx'),c=pid('prx'),d=pid('prx');grid.insertAdjacentHTML('afterend',poHeading('风险预算')+'<div class="panel-grid">'+panel(a,'波动与尾部风险','')+panel(b,'累计风险解释','')+panel(c,'约束分类','')+panel(d,'参数分类','')+'</div>');const profiles=arr(obj(data.asset_pool).profiles).filter(function(r){return r.asset_type==='ETF';}),ev=arr(obj(risk.covariance).eigenvalues).slice().sort(function(x,y){return y-x;}),total=ev.reduce(function(x,y){return x+Number(y);},0);let cum=0;plot(a,[{type:'scatter',mode:'markers+text',x:profiles.map(function(r){return Number(r.annual_volatility_1y)*100;}),y:profiles.map(function(r){return Number(r.daily_cvar_95)*100;}),text:profiles.map(function(r){return r.name||r.code;}),textposition:'top center',marker:{size:11,color:profiles.map(function(r){return Number(r.target_weight||0)*100;}),colorscale:'Portland',showscale:true,colorbar:{title:'权重%'}}}],{height:360,xaxis:{title:'年化波动（%）'},yaxis:{title:'日度CVaR 95%（%）'}});plot(b,[{type:'scatter',mode:'lines+markers',x:ev.map(function(_,i){return i+1;}),y:ev.map(function(v){cum+=Number(v);return total?cum/total*100:0;}),fill:'tozeroy',line:{color:'#2f75b5',width:2.4}}],{height:360,xaxis:{title:'主成分数量'},yaxis:{title:'累计解释（%）',range:[0,105]}});const cons=arr(risk.constraints),cats=poUnique(cons,'category');plot(c,[{type:'bar',x:cats,y:cats.map(function(k){return cons.filter(function(r){return r.category===k;}).length;}),marker:{color:'#b42318'}}],{height:360,xaxis:{tickangle:-30},yaxis:{title:'约束数'}});const groups=poUnique(parameters,'group');plot(d,[{type:'bar',orientation:'h',x:groups.map(function(k){return parameters.filter(function(r){return r.group===k;}).length;}),y:groups,marker:{color:'#c46a08'}}],{height:360,xaxis:{title:'参数数'},yaxis:{automargin:true}});
  }
  async function portfolioSolveR43(){
    await portfolioSolve();const data=await needPortfolio(),opt=obj(data.optimization),pane=document.querySelector('.view-cache-pane[data-view="'+S.active+'"]');if(!pane)return;const kpi=pane.querySelector('.kpi-grid'),grid=pane.querySelector('.panel-grid'),tables=Array.from(pane.querySelectorAll('.table-panel')),spec=obj(opt.selected_spec);$('core-conclusion').classList.add('portfolio-conclusion');conclusion('训练集筛选、验证集固定的当前方案为 '+esc(poPlanName(spec))+'；测试集不参与调参。当前解由 '+esc(obj(data.home).selected_solver.solver||'--')+' 求得，约束残差 '+Number(obj(opt.constraint_slack).max_violation||0).toExponential(2)+'。');poInsertHeading(kpi,'求解结论');poInsertHeading(grid,'权重与约束');if(tables[0])poInsertHeading(tables[0],'求解质量');
    kpi.insertAdjacentHTML('beforebegin','<section class="control-card"><div class="control-grid"><label>候选显示<select id="po-solve-top"><option value="10">前10</option><option value="20">前20</option><option value="30">前30</option></select></label><div class="control-readout">当前方案<strong>'+esc(poPlanName(spec))+'</strong></div><div class="control-readout">候选数量<strong>'+obj(data.method).candidate_count+'</strong></div></div></section>');$('po-solve-top').value=String(S.portfolio.topN);
    if(tables[1]){const rows=arr(opt.leaderboard).slice(0,S.portfolio.topN).map(function(r){return {candidate_id:poPlanName(r),status:r.status,risk_aversion:r.risk_aversion,turnover_l2:r.turnover_l2,position_cap:poPct(r.position_cap),train_sharpe:poNum(r.train_sharpe),validation_sharpe:poNum(r.validation_sharpe),validation_score:poNum(r.validation_score),validation_max_drawdown:poPct(r.validation_max_drawdown)};});tables[1].insertAdjacentHTML('beforebegin',poTableHTML('候选比较',rows,['candidate_id','status','risk_aversion','turnover_l2','position_cap','train_sharpe','validation_sharpe','validation_score','validation_max_drawdown']));tables[1].remove();}
    const a=pid('psx'),b=pid('psx'),c=pid('psx'),d=pid('psx');grid.insertAdjacentHTML('afterend',poHeading('候选比较')+'<div class="panel-grid">'+panel(a,'求解器效率','')+panel(b,'预期收益与波动','')+panel(c,'权重集中度','')+panel(d,'候选参数分布','')+'</div>');const sol=arr(opt.solver_benchmark),weights=arr(opt.current_weights),leaders=arr(opt.leaderboard).slice(0,S.portfolio.topN);plot(a,[{type:'bar',name:'耗时',x:sol.map(function(r){return r.solver;}),y:sol.map(function(r){return Number(r.median_ms);}),marker:{color:sol.map(function(r){return r.status==='optimal'?'#168a47':'#b42318';})}},{type:'scatter',mode:'markers',name:'残差',x:sol.map(function(r){return r.solver;}),y:sol.map(function(r){return r.max_constraint_violation==null?null:Math.max(Number(r.max_constraint_violation),1e-12);}),yaxis:'y2',marker:{size:12,color:'#2f75b5'}}],{height:360,yaxis:{title:'耗时（ms）'},yaxis2:{title:'残差',type:'log',overlaying:'y',side:'right'},legend:{orientation:'h',y:-.2}});plot(b,[{type:'scatter',mode:'markers+text',x:weights.map(function(r){return Number(r.annual_volatility)*100;}),y:weights.map(function(r){return Number(r.expected_return)*100;}),text:weights.map(function(r){return r.name||r.code;}),textposition:'top center',marker:{size:weights.map(function(r){return 8+Number(r.weight)*50;}),color:weights.map(function(r){return Number(r.weight);}),colorscale:'Portland',showscale:true,colorbar:{title:'权重'}}}],{height:360,xaxis:{title:'预期波动（%）'},yaxis:{title:'预期收益（%）'}});const sorted=weights.slice().sort(function(x,y){return Number(y.weight)-Number(x.weight);});let cumulative=0;plot(c,[{type:'bar',x:sorted.map(function(r){return r.name||r.code;}),y:sorted.map(function(r){return Number(r.weight)*100;}),marker:{color:'#b42318'}},{type:'scatter',mode:'lines+markers',x:sorted.map(function(r){return r.name||r.code;}),y:sorted.map(function(r){cumulative+=Number(r.weight);return cumulative*100;}),yaxis:'y2',line:{color:'#2f75b5',width:2.2}}],{height:360,xaxis:{tickangle:-35},yaxis:{title:'权重（%）'},yaxis2:{title:'累计权重（%）',overlaying:'y',side:'right'}});plot(d,[{type:'parcoords',line:{color:leaders.map(function(r){return Number(r.validation_score);}),colorscale:'Portland',showscale:true},dimensions:[{label:'回看',values:leaders.map(function(r){return Number(r.lookback_days);})},{label:'风险厌恶',values:leaders.map(function(r){return Number(r.risk_aversion);})},{label:'换手惩罚',values:leaders.map(function(r){return Number(r.turnover_l2);})},{label:'上限',values:leaders.map(function(r){return Number(r.position_cap);})},{label:'验证夏普',values:leaders.map(function(r){return Number(r.validation_sharpe);})}]}],{height:360,margin:{l:55,r:55,t:35,b:35}});$('po-solve-top').onchange=function(){S.portfolio.topN=Number(this.value);portfolioSolveR43();};
  }
  async function portfolioBacktestR43(){
    await portfolioBacktest();const data=await needPortfolio(),pane=document.querySelector('.view-cache-pane[data-view="'+S.active+'"]');if(!pane)return;const controls=pane.querySelector('.control-card'),first=pane.querySelector('.chart-panel.wide'),grid=pane.querySelector('.panel-grid'),table=pane.querySelector('.table-panel');$('core-conclusion').classList.add('portfolio-conclusion');poInsertHeading(controls,'查看条件');poInsertHeading(first,'收益与回撤');poInsertHeading(table,'指标明细');const a=pid('pbx'),b=pid('pbx'),c=pid('pbx'),d=pid('pbx'),e=pid('pbx');grid.insertAdjacentHTML('afterend',poHeading('交易与稳健性')+'<div class="panel-grid">'+panel(a,'相对等权净值','')+panel(b,'12月滚动波动','')+panel(c,'年度收益','')+panel(d,'成本敏感性','')+panel(e,'压力情景','',true)+'</div>');
    const strategies=obj(data.backtest).strategies,sel=poWindowRows(arr(obj(strategies.selected).nav),S.portfolio.window),eqMap=new Map(arr(obj(strategies.equal_weight).nav).map(function(r){return [r.date,r];}));let rel=1;plot(a,[{type:'scatter',mode:'lines',x:sel.map(function(r){return poDate(r.date);}),y:sel.map(function(r){const eq=eqMap.get(r.date);rel*=eq?(1+Number(r.period_return))/(1+Number(eq.period_return)):1;return rel;}),fill:'tozeroy',line:{color:'#b42318',width:2.4}}],{height:360,xaxis:{type:'date'},yaxis:{title:'相对净值'}});plot(b,S.portfolio.strategies.map(function(key){const rows=poWindowRows(arr(obj(strategies[key]).nav),S.portfolio.window);return {type:'scatter',mode:'lines',name:PO_STRATEGY_CN[key],x:rows.map(function(r){return poDate(r.date);}),y:rows.map(function(r){return Number(r.rolling_volatility_12m)*100;}),connectgaps:false};}),{height:360,xaxis:{type:'date'},yaxis:{title:'年化波动（%）'},legend:{orientation:'h',y:-.25}});
    const years=poUnique(sel.map(function(r){return {year:String(r.date).slice(0,4)};}),'year').sort(),annual=years.map(function(year){return sel.filter(function(r){return String(r.date).startsWith(year);}).reduce(function(v,r){return v*(1+Number(r.period_return));},1)-1;});plot(c,[{type:'bar',x:years,y:annual.map(function(v){return v*100;}),marker:{color:annual.map(function(v){return v>=0?'#b42318':'#168a47';})},text:annual.map(function(v){return poPct(v,1);}),textposition:'auto'}],{height:360,yaxis:{title:'年度收益（%）'}});const costs=arr(obj(data.backtest).cost_sensitivity_test);plot(d,[{type:'scatter',mode:'lines+markers',name:'年化收益',x:costs.map(function(r){return r.cost_bps;}),y:costs.map(function(r){return Number(r.annual_return)*100;}),line:{color:'#b42318',width:2.4}},{type:'scatter',mode:'lines+markers',name:'夏普',x:costs.map(function(r){return r.cost_bps;}),y:costs.map(function(r){return Number(r.sharpe);}),yaxis:'y2',line:{color:'#2f75b5',width:2.4}}],{height:360,xaxis:{title:'单边成本（bp）'},yaxis:{title:'年化收益（%）'},yaxis2:{title:'夏普',overlaying:'y',side:'right'},legend:{orientation:'h',y:-.2}});const stress=arr(obj(data.backtest).stress_scenarios);plot(e,[{type:'bar',name:'当前组合',x:stress.map(function(r){return r.scenario;}),y:stress.map(function(r){return Number(r.return)*100;}),marker:{color:'#b42318'}},{type:'bar',name:'等权基准',x:stress.map(function(r){return r.scenario;}),y:stress.map(function(r){return Number(r.benchmark_return)*100;}),marker:{color:'#98a2b3'}}],{height:360,barmode:'group',yaxis:{title:'区间收益（%）'},legend:{orientation:'h',y:-.2}});
  }
  async function renderPortfolio(view){if(view==='pool')return await portfolioPoolR43();if(view==='risk')return await portfolioRiskR43();if(view==='solve')return await portfolioSolveR43();if(view==='backtest')return await portfolioBacktestR43();return await portfolioHomeR43();}


  /* 2026-07-23 research-workspace navigation. This final dispatcher keeps every
     legacy visualization reachable while presenting the new seven-module layout. */
  Object.assign(VIEW_BREADCRUMBS,{
    home:{title:'主页',views:{overview:'综合研判'}},
    data:{title:'数据看板',views:{macro:'宏观',global_markets:'全球市场',sw_industries:'行业',commodities:'大宗商品',stock:'个股',news_events:'新闻事件',ai_monitor:'AI监控'}},
    allocation:{title:'资产配置',views:{cycle:'周期跟踪',strategy:'配置策略'}},
    liquidity:{title:'资金面跟踪',views:{retail:'散户',public:'公募',private:'私募',foreign:'外资',etf:'ETF',primary:'一级市场',margin:'融资资金'}},
    rotation:{title:'行业景气度',views:{home:'01主页',industry:'02行业景气度',style:'03风格轮动周期',allocation:'04配置策略',backtest:'05策略回测'}},
    factorlab:{title:'因子实验室',views:{dashboard:'因子看板',mining:'因子挖掘',strategy:'配置策略'}},
    technical:{title:'技术分析',views:{learning:'K线学习',strategy:'配置策略'}},
    portfolio:{title:'组合优化',views:{solve:'优化求解',strategy:'配置策略'}}
  });

  S.workspace=S.workspace||{frequency:'daily',risk:'balanced',section:{}};
  let workspaceControlQueue=Promise.resolve(),workspaceControlPending=0;
  const WORKSPACE_CONFIG={
    'home:overview':{group:'主页',title:'每日策略总览',subtitle:'数据点评与资产配置—资金—行业—个股—组合联动输出',sections:[{id:'overview',label:'综合研判',kind:'home'}]},
    'data:macro':{group:'数据看板',title:'宏观',subtitle:'增长、通胀、流动性与实体高频',sections:[{id:'macro',label:'宏观',kind:'data',page:'macro'}]},
    'data:global_markets':{group:'数据看板',title:'全球市场',subtitle:'主要市场收益、波动与回撤',sections:[{id:'global',label:'全球市场',kind:'data',page:'global_markets'}]},
    'data:sw_industries':{group:'数据看板',title:'行业',subtitle:'申万一级行业行情与相对强弱',sections:[{id:'industry',label:'行业',kind:'data',page:'sw_industries'}]},
    'data:commodities':{group:'数据看板',title:'大宗商品',subtitle:'期现价格、基差、趋势与风险',sections:[{id:'commodities',label:'大宗商品',kind:'data',page:'commodities'}]},
    'data:stock':{group:'数据看板',title:'个股',subtitle:'行情、风险收益、新闻与智能分析',sections:[{id:'stock',label:'个股',kind:'data',page:'stock'}]},
    'data:news_events':{group:'数据看板',title:'新闻事件',subtitle:'行业与个股事件流',sections:[{id:'news',label:'新闻事件',kind:'data',page:'news_events'}]},
    'data:ai_monitor':{group:'数据看板',title:'AI监控',subtitle:'技术扩散与人工智能产业监控',sections:[{id:'monitor',label:'AI监控',kind:'ai-monitor'}]},
    'allocation:cycle':{group:'资产配置',title:'周期跟踪',subtitle:'普林格、基钦、朱格拉、康波与美林时钟',sections:[{id:'cycle',label:'周期跟踪',kind:'allocation',page:'cycle'}]},
    'allocation:strategy':{group:'资产配置',title:'配置策略',subtitle:'综合配置、权重方案与回测审计',sections:[{id:'overview',label:'综合配置',kind:'allocation',page:'home'},{id:'weights',label:'权重方案',kind:'allocation',page:'strategy'},{id:'backtest',label:'回测检验',kind:'allocation',page:'backtest'}]},
    'liquidity:retail':{group:'资金面跟踪',title:'散户',subtitle:'资金总览、小单流、开户与参与度',sections:[{id:'overview',label:'资金总览',kind:'liquidity',page:'home'},{id:'retail',label:'散户',kind:'liquidity',page:'retail'}]},
    'liquidity:public':{group:'资金面跟踪',title:'公募',subtitle:'新发、仓位、报会与清算',sections:[{id:'public',label:'公募',kind:'liquidity',page:'public'}]},
    'liquidity:private':{group:'资金面跟踪',title:'私募',subtitle:'仓位、净流入与策略指数',sections:[{id:'private',label:'私募',kind:'liquidity',page:'private'}]},
    'liquidity:foreign':{group:'资金面跟踪',title:'外资',subtitle:'配置流、陆股通成交与A/H分配',sections:[{id:'foreign',label:'外资',kind:'liquidity',page:'foreign'}]},
    'liquidity:etf':{group:'资金面跟踪',title:'ETF',subtitle:'份额申赎、资金流与结构分解',sections:[{id:'etf',label:'ETF',kind:'liquidity',page:'etf'}]},
    'liquidity:primary':{group:'资金面跟踪',title:'一级市场',subtitle:'IPO、定增与可转债融资供给',sections:[{id:'primary',label:'一级市场',kind:'liquidity',page:'primary'}]},
    'liquidity:margin':{group:'资金面跟踪',title:'融资资金',subtitle:'净买入、余额、活跃度与担保结构',sections:[{id:'margin',label:'融资资金',kind:'liquidity',page:'margin'}]},
    'rotation:home':{group:'行业景气度',title:'01主页',subtitle:'31个申万一级行业 × 12个季度个股风格箱总览',sections:[{id:'overview',label:'01主页',kind:'rotation',page:'home'}]},
    'rotation:industry':{group:'行业景气度',title:'02行业景气度',subtitle:'31个申万一级行业专属景气指标',sections:[{id:'industry',label:'02行业景气度',kind:'rotation',page:'industry'}]},
    'rotation:style':{group:'行业景气度',title:'03风格轮动周期',subtitle:'大/中/小盘 × 成长/均衡/价值/红利 · 季度个股标签',sections:[{id:'style',label:'03风格轮动周期',kind:'rotation',page:'style'}]},
    'rotation:allocation':{group:'行业景气度',title:'04配置策略',subtitle:'行业Top10（月/周）× 风格Top3（季度）',sections:[{id:'weights',label:'04配置策略',kind:'rotation',page:'allocation'}]},
    'rotation:backtest':{group:'行业景气度',title:'05策略回测',subtitle:'训练集、验证集、测试集与同频等权基准',sections:[{id:'backtest',label:'05策略回测',kind:'rotation',page:'backtest'}]},
    'factorlab:dashboard':{group:'因子实验室',title:'因子看板',subtitle:'因子状态、联合检验与历史任务',sections:[{id:'overview',label:'实验室总览',kind:'factorlab',page:'home'},{id:'dashboard',label:'因子看板',kind:'factorlab',page:'dashboard'},{id:'testing',label:'联合检验',kind:'factorlab',page:'testing'},{id:'history',label:'历史任务',kind:'factorlab',page:'history'}]},
    'factorlab:mining':{group:'因子实验室',title:'因子挖掘',subtitle:'实验挖掘与LLM假设—表达—检验—记忆闭环',sections:[{id:'laboratory',label:'实验挖掘',kind:'factorlab',page:'mining'},{id:'llm-home',label:'LLM挖掘',kind:'factor',page:'home'},{id:'expression',label:'因子表达式',kind:'factor',page:'expression'},{id:'report',label:'检验结果',kind:'factor',page:'report'},{id:'score',label:'综合打分',kind:'factor',page:'score'},{id:'memory',label:'历史记忆',kind:'factor',page:'memory'}]},
    'factorlab:strategy':{group:'因子实验室',title:'配置策略',subtitle:'因子策略与指数增强完整配置链',sections:[{id:'factor-strategy',label:'因子策略',kind:'factorlab',page:'strategy'},{id:'index-home',label:'增强总览',kind:'index',page:'home'},{id:'universe',label:'资产池',kind:'index',page:'universe'},{id:'alpha',label:'Alpha模型',kind:'index',page:'alpha'},{id:'smartbeta',label:'SmartBeta',kind:'index',page:'smartbeta'},{id:'risk',label:'风险模型',kind:'index',page:'risk'},{id:'tracking',label:'组合跟踪',kind:'index',page:'tracking'}]},
    'technical:learning':{group:'技术分析',title:'K线学习',subtitle:'任务设置、同类学习、情境记忆与历史记录',sections:[{id:'setup',label:'任务设置',kind:'kline',page:'home'},{id:'learning',label:'学习记忆',kind:'kline',page:'learn'},{id:'history',label:'历史记录',kind:'kline',page:'history'}]},
    'technical:strategy':{group:'技术分析',title:'配置策略',subtitle:'学习后策略、信号K线与回测归因',sections:[{id:'backtest',label:'策略回测',kind:'kline',page:'backtest'}]},
    'portfolio:solve':{group:'组合优化',title:'优化求解',subtitle:'候选方案、求解器、权重与约束',sections:[{id:'solve',label:'优化求解',kind:'portfolio',page:'solve'}]},
    'portfolio:strategy':{group:'组合优化',title:'配置策略',subtitle:'组合总览、资产池、风险约束与回测审计',sections:[{id:'overview',label:'组合总览',kind:'portfolio',page:'home'},{id:'pool',label:'资产池',kind:'portfolio',page:'pool'},{id:'risk',label:'风险约束',kind:'portfolio',page:'risk'},{id:'backtest',label:'组合回测',kind:'portfolio',page:'backtest'}]}
  };
  const LEGACY_ROUTE_ALIAS={
    'allocation:home':['allocation:strategy','overview'],'allocation:backtest':['allocation:strategy','backtest'],
    'liquidity:home':['liquidity:retail','overview'],
    'portfolio:home':['portfolio:strategy','overview'],'portfolio:pool':['portfolio:strategy','pool'],'portfolio:risk':['portfolio:strategy','risk'],'portfolio:backtest':['portfolio:strategy','backtest'],
    'index:home':['factorlab:strategy','index-home'],'index:universe':['factorlab:strategy','universe'],'index:alpha':['factorlab:strategy','alpha'],'index:smartbeta':['factorlab:strategy','smartbeta'],'index:risk':['factorlab:strategy','risk'],'index:tracking':['factorlab:strategy','tracking'],
    'factorlab:home':['factorlab:dashboard','overview'],'factorlab:testing':['factorlab:dashboard','testing'],'factorlab:history':['factorlab:dashboard','history'],
    'factor:home':['factorlab:mining','llm-home'],'factor:expression':['factorlab:mining','expression'],'factor:report':['factorlab:mining','report'],'factor:score':['factorlab:mining','score'],'factor:memory':['factorlab:mining','memory'],
    'kline:home':['technical:learning','setup'],'kline:learn':['technical:learning','learning'],'kline:history':['technical:learning','history'],'kline:backtest':['technical:strategy','backtest']
  };

  function workspaceConfig(){return WORKSPACE_CONFIG[S.active]||WORKSPACE_CONFIG['home:overview'];}
  function workspaceSection(config){
    const wanted=S.workspace.section[S.active];
    return config.sections.find(function(item){return item.id===wanted;})||config.sections[0];
  }
  function workspaceStatusText(){
    const services=(S.services&&S.services.services)||{},bad=Object.keys(services).filter(function(key){return st(services[key].status||services[key].snapshot_status)==='failed';});
    return bad.length?'存在 '+bad.length+' 项服务异常':'全部数据链路正常';
  }
  function workspaceQueueAction(route,action){
    workspaceControlPending+=1;
    const mark=function(){const host=$('workspace-controls');if(host)host.setAttribute('aria-busy','true');};
    mark();
    workspaceControlQueue=workspaceControlQueue.catch(function(error){console.error('工作区控件队列异常',error);}).then(async function(){
      if(S.active!==route)return;
      mark();
      await action();
    }).finally(function(){
      workspaceControlPending=Math.max(0,workspaceControlPending-1);
      const host=$('workspace-controls');
      if(host){if(workspaceControlPending)host.setAttribute('aria-busy','true');else host.removeAttribute('aria-busy');}
    });
    return workspaceControlQueue;
  }
  function workspaceRenderControls(config){
    const host=$('workspace-controls');if(!host)return;
    const selected=workspaceSection(config),asOf=(S.snapshot&&S.snapshot.as_of)||'最新可用';
    host.innerHTML='<div class="workspace-global-controls"><label>更新频率<select id="workspace-frequency"><option value="daily">日度</option><option value="weekly">周度</option><option value="monthly">月度</option></select></label>'+
      '<label>风险偏好<select id="workspace-risk"><option value="conservative">稳健</option><option value="balanced">平衡</option><option value="aggressive">进取</option></select></label>'+
      '<label>数据日期<select id="workspace-asof"><option value="latest">'+esc(asOf)+'</option></select></label>'+
      '<div class="workspace-health"><span class="status-dot '+(workspaceStatusText().includes('异常')?'failed':'ok')+'"></span><strong>'+esc(workspaceStatusText())+'</strong></div>'+
      '<button id="workspace-refresh" class="ghost-button" type="button">刷新快照</button></div>'+
      '<nav class="workspace-section-nav" aria-label="当前板块功能">'+config.sections.map(function(item){return '<button type="button" data-workspace-section="'+esc(item.id)+'" class="'+(item.id===selected.id?'is-active':'')+'">'+esc(item.label)+'</button>';}).join('')+'</nav>';
    $('workspace-frequency').value=S.workspace.frequency;$('workspace-risk').value=S.workspace.risk;
    $('workspace-frequency').onchange=function(){const route=S.active,value=this.value;workspaceQueueAction(route,async function(){S.workspace.frequency=value;if(window.IndustryRotation&&window.IndustryRotation.state)window.IndustryRotation.state.frequency=value==='monthly'?'monthly':'weekly';invalidateView(route);await render(true);});};
    $('workspace-risk').onchange=function(){const route=S.active,value=this.value;workspaceQueueAction(route,async function(){S.workspace.risk=value;S.allocation.riskProfile={conservative:'conservative',balanced:'balanced',aggressive:'equity_preferred'}[value];invalidateView(route);await render(true);});};
    host.querySelectorAll('[data-workspace-section]').forEach(function(button){button.onclick=function(){const route=S.active,sectionId=this.dataset.workspaceSection;if(sectionId===workspaceSection(config).id)return;workspaceQueueAction(route,async function(){S.workspace.section[route]=sectionId;invalidateView(route);await render(true);window.scrollTo({top:0,left:0,behavior:'auto'});});};});
    $('workspace-refresh').onclick=workspaceRefresh;
  }
  async function workspaceRefresh(){
    const button=$('workspace-refresh');if(button){button.disabled=true;button.textContent='刷新中…';}
    S.snapshot=null;S.services=null;S.seriesCache={};S.globalSupp=null;S.sw=null;S.cmdty=null;S.stockOverride=null;
    if(S.allocation)S.allocation.snapshot=null;if(S.portfolio)S.portfolio.snapshot=null;if(S.liquidity)S.liquidity.snapshot=null;
    if(window.IndexEnhancement&&window.IndexEnhancement.state)window.IndexEnhancement.state.snapshot=null;
    if(window.IndustryRotation&&window.IndustryRotation.state){window.IndustryRotation.state.snapshot=null;window.IndustryRotation.state.tracking=null;}
    Array.from(VIEW_CACHE.keys()).forEach(dropView);displayedView=null;
    await Promise.allSettled([loadServices(),loadSnapshot()]);await render(true);
  }
  function workspaceRestoreHeading(config){
    setText('page-eyebrow',config.group+' > '+config.title+' >');setText('page-title',config.title);setText('page-subtitle',config.subtitle||'');
    VIEW_META.set(S.active,Object.assign({},VIEW_META.get(S.active)||{},{title:config.title,subtitle:config.subtitle||'',eye:config.group+' > '+config.title+' >'}));
    document.body.dataset.workspaceRoute=S.active;
  }
  function workspaceSyncNav(){
    document.querySelectorAll('.nav-item').forEach(function(item){item.classList.toggle('is-active',item.dataset.target===S.active);});
    const active=document.querySelector('.nav-item.is-active');if(active&&active.closest('.nav-group')){const group=active.closest('.nav-group'),toggle=group.querySelector('.nav-group-toggle'),children=group.querySelector('.nav-children');if(toggle)toggle.setAttribute('aria-expanded','true');if(children)children.hidden=false;}
  }
  function workspaceApplySharedParameters(){
    const risk={conservative:'conservative',balanced:'balanced',aggressive:'aggressive'}[S.workspace.risk];
    if($('kp'))$('kp').value=risk;if($('alloc-risk'))$('alloc-risk').value=S.allocation.riskProfile;
  }

  function workspaceAiMonitor(){
    header('AI监控','技术扩散与人工智能产业监控','数据看板');
    conclusion('AI监控作为数据看板的独立证据页接入；页面按需加载，若嵌入受浏览器策略限制，可使用右上角按钮打开原始公网界面。');
    root('<section class="ai-monitor-shell"><header><div><span>外部实时看板</span><h2>技术扩散监控</h2></div><a class="action-button" href="https://desktop-i22b489.tailf9d7ac.ts.net/tech-diffusion/" target="_blank" rel="noopener">打开独立页面</a></header><iframe title="AI技术扩散监控" src="https://desktop-i22b489.tailf9d7ac.ts.net/tech-diffusion/" loading="lazy" referrerpolicy="same-origin"><a href="https://desktop-i22b489.tailf9d7ac.ts.net/tech-diffusion/">打开AI监控</a></iframe></section>');
  }
  function workspaceTopBy(rows,key){return arr(rows).slice().sort(function(a,b){return Number(b[key]||-1e12)-Number(a[key]||-1e12);})[0]||{};}
  function workspaceWeightChips(rows,nameKey,weightKey){
    const shown=arr(rows).filter(function(row){return Number(row[weightKey])>.0005;}).slice(0,12);
    return shown.map(function(row){return '<span><strong>'+esc(row[nameKey]||row.code||'--')+'</strong>'+fmt(Number(row[weightKey])*100,1)+'%</span>';}).join('')||'<span>暂无可用权重</span>';
  }
  async function workspaceHome(){
    header('每日策略总览','数据点评与资产配置—资金—行业—个股—组合联动输出','主页');
    const results=await Promise.allSettled([needAllocation(),needLiquidity(),needPortfolio(),api('/api/rotation/snapshot')]);
    const value=function(i){return results[i].status==='fulfilled'?results[i].value:{};},allocation=value(0),liquidity=value(1),portfolio=value(2),rotation=value(3);
    const globalRows=arr(table('global_markets','global_market_matrix').rows),industryRows=arr(table('sw_industries','sw_l1_full_snapshot').rows),commodityRows=arr(table('commodities','commodity_market_matrix').rows),stockRows=arr(table('stock','stock_watchlist').rows),newsRows=arr(table('news_events','news_feed').rows).slice().sort(function(a,b){return String(b.published_at).localeCompare(String(a.published_at));});
    const globalTop=workspaceTopBy(globalRows,'ret_5d'),industryTop=workspaceTopBy(industryRows,'ret_5d'),commodityTop=workspaceTopBy(commodityRows,'ret_20d'),stockTop=workspaceTopBy(stockRows,'ret_20d'),latestNews=newsRows[0]||{};
    const profile={conservative:'conservative',balanced:'balanced',aggressive:'equity_preferred'}[S.workspace.risk],assetWeights=allocation&&allocation.allocations?allocWeights(allocation,'recommended',profile):{};
    const monthly=obj(obj(rotation.industry).frequencies).monthly||{},weekly=obj(obj(rotation.industry).frequencies).weekly||{},styleQuarterly=obj(obj(rotation.style).frequencies).quarterly||{};
    const monthlyHolding=arr(monthly.holdings).slice(-1)[0]||{},weeklyHolding=arr(weekly.holdings).slice(-1)[0]||{},styleHolding=arr(styleQuarterly.holdings).slice(-1)[0]||{};
    const finalWeights=arr(obj(portfolio.home).current_weights).slice().sort(function(a,b){return Number(b.weight)-Number(a.weight);});
    const liqPages=obj(liquidity.pages),liqText=['retail','public','private','foreign','etf','primary','margin'].map(function(key){return obj(liqPages[key]).conclusion;}).filter(Boolean).slice(0,3).join('；');
    const assetRows=[{name:'权益',weight:Number(assetWeights.equity||0)},{name:'债券',weight:Number(assetWeights.bond||0)},{name:'商品',weight:Number(assetWeights.commodity||0)},{name:'现金',weight:Number(assetWeights.cash||0)}];
    const monthIndustries=arr(monthlyHolding.names).map(function(name){return {name:name,weight:Number(monthlyHolding.weight||0)};}),weekIndustries=arr(weeklyHolding.names).map(function(name){return {name:name,weight:Number(weeklyHolding.weight||0)};});
    const styleRows=arr(styleHolding.names).map(function(name){return {name:name,weight:Number(styleHolding.weight||0)};});
    conclusion('数据、资产配置、资金面、行业风格、个股观察和组合优化已在同一页联动；<strong>红色重点</strong>表示本期最需要复核的方向，所有权重均直接来自现有模型快照。');
    root('<div class="research-home"><section class="research-brief"><header><span>日度研究观点</span><time>'+esc((S.snapshot&&S.snapshot.as_of)||'--')+'</time></header>'+
      '<p><strong>宏观与全球：</strong>宏观高频表覆盖 '+arr(table('macro','macro_latest').rows).length+' 项指标；全球市场近一周相对领先为 <b class="brief-red">'+esc(globalTop.market||'--')+' '+signed(globalTop.ret_5d||0)+'%</b>，需同时结合其20日波动与60日回撤判断风险。</p>'+
      '<p><strong>行业与商品：</strong>申万行业近一周相对领先为 <b class="brief-red">'+esc(industryTop.industry||'--')+' '+signed(industryTop.ret_5d||0)+'%</b>；商品20日相对领先为 <b>'+esc(commodityCN(commodityTop.symbol||'--'))+' '+signed(commodityTop.ret_20d||0)+'%</b>，趋势信号不替代配置模型。</p>'+
      '<p><strong>个股与事件：</strong>观察池20日相对领先为 <b class="brief-red">'+esc(stockTop.name||stockTop.code||'--')+' '+signed(stockTop.ret_20d||0)+'%</b>；最新事件为“'+esc(latestNews.title||'暂无')+'”，来源 '+esc(latestNews.source||'--')+'。</p>'+
      '<p><strong>资金风险提示：</strong>'+esc(liqText||'资金面快照已加载；请在七类资金页逐项复核。')+'</p></section>'+
      '<section class="horizon-grid"><article><header><span>日度</span><strong>组合优化执行篮子</strong></header><p>以资金状态和个股事件作为风险门，最终采用优化器当前可行权重；仅展示非零头寸。</p><div class="weight-chips">'+workspaceWeightChips(finalWeights,'name','weight')+'</div></article>'+
      '<article><header><span>周度 / 季度</span><strong>行业与风格联动</strong></header><p>行业Top10按周度模型输出，风格Top3按季度模型输出；测试集只报告、不参与选模，季度信息不回填周度历史。</p><h4>行业</h4><div class="weight-chips">'+workspaceWeightChips(weekIndustries,'name','weight')+'</div><h4>风格</h4><div class="weight-chips">'+workspaceWeightChips(styleRows,'name','weight')+'</div></article>'+
      '<article><header><span>月度</span><strong>大类资产与行业权重</strong></header><p>大类资产采用当前风险偏好档位；行业采用月度景气模型最新持仓。</p><h4>大类资产</h4><div class="weight-chips">'+workspaceWeightChips(assetRows,'name','weight')+'</div><h4>行业</h4><div class="weight-chips">'+workspaceWeightChips(monthIndustries,'name','weight')+'</div></article></section>'+
      '<section class="research-linkage"><h2>联动顺序</h2><div><span>01 数据看板</span><span>02 资产配置</span><span>03 资金面</span><span>04 行业与风格</span><span>05 个股选择</span><span>06 组合优化</span></div></section></div>');
  }

  async function workspaceRenderSection(section){
    if(section.kind==='home')return workspaceHome();
    if(section.kind==='ai-monitor')return workspaceAiMonitor();
    if(section.kind==='data')return renderData(section.page);
    if(section.kind==='allocation')return renderAllocation(section.page);
    if(section.kind==='liquidity')return renderLiquidity(section.page);
    if(section.kind==='portfolio')return renderPortfolio(section.page);
    if(section.kind==='kline')return renderKline(section.page);
    if(section.kind==='factor')return renderFactor(section.page);
    displayedView=null;
    if(section.kind==='rotation'&&window.IndustryRotation)return window.IndustryRotation.render(section.page);
    if(section.kind==='factorlab'&&window.FactorLaboratory)return window.FactorLaboratory.render(section.page);
    if(section.kind==='index'&&window.IndexEnhancement)return window.IndexEnhancement.render(section.page);
    throw new Error('功能模块尚未加载：'+section.kind);
  }

  async function renderWorkspace(force){
    const alias=LEGACY_ROUTE_ALIAS[S.active];
    if(alias){S.active=alias[0];S.workspace.section[S.active]=alias[1];}
    const config=workspaceConfig();workspaceSyncNav();workspaceRenderControls(config);
    const section=workspaceSection(config),external=['rotation','factorlab','index'].includes(section.kind);
    if(!external&&!force&&showCachedView(S.active)){workspaceRestoreHeading(config);workspaceApplySharedParameters();return;}
    if(force){
      invalidateView(S.active);
      const loadingHost=$('view-root');
      if(loadingHost)loadingHost.innerHTML='<div class="loading-card">正在载入对应模型、图表和研究结论，请稍候。</div>';
    }
    if(external){displayedView=null;const host=$('view-root');if(host)host.innerHTML='<div class="loading-card">正在载入合并后的功能板块。</div>';}
    try{await workspaceRenderSection(section);}catch(error){console.error('工作区渲染异常',error);conclusion('页面加载失败：'+esc(error.message));root('<div class="loading-card">当前功能暂不可用，请检查服务状态后重试。</div>');}
    workspaceRestoreHeading(config);workspaceRenderControls(config);workspaceApplySharedParameters();applyNavStatuses();
  }
  S.active='home:overview';
  render=renderWorkspace;
  function sleep(ms){ return new Promise(r=>setTimeout(r,ms)); }
})();
