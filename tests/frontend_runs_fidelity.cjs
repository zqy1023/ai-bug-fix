const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert=require('node:assert/strict');
const {mkdirSync,writeFileSync}=require('node:fs');
const origin=process.env.UI_ORIGIN||'http://127.0.0.1:5173';
const dir=process.env.UI_ARTIFACT_DIR||'.stitch/verification/fidelity';
const stamp=new Date().toISOString();
const base={status:'running',terminal:false,started_at:stamp,task_count:12,succeeded_tasks:3,failed_tasks:0,blocked_tasks:1,manual_tasks:0,waiting_tasks:2,inactive_tasks:0};
const runs=[{...base,run_id:'RUN-1025-0830'},{...base,run_id:'RUN-1025-0700',task_count:6,succeeded_tasks:2,blocked_tasks:0,waiting_tasks:0},{...base,run_id:'RUN-1024-0830',status:'succeeded',terminal:true,task_count:18,succeeded_tasks:18,blocked_tasks:0,waiting_tasks:0},{...base,run_id:'RUN-1023-1400',status:'failed',terminal:true,task_count:8,succeeded_tasks:5,failed_tasks:1,blocked_tasks:0,waiting_tasks:0}];
(async()=>{
 mkdirSync(dir,{recursive:true});const browser=await chromium.launch({headless:true,channel:'msedge'});
 const page=await browser.newPage({viewport:{width:1280,height:1024},deviceScaleFactor:2});const errors=[],requests=[],methods=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/api/v1/**',async route=>{
  const request=route.request(),url=new URL(request.url()),path=url.pathname;requests.push(url);methods.push(request.method());
  let body={items:[],total:0};
  if(path.endsWith('/health'))body={status:'ok',service:'fidelity-fixture',api_token_required:false};
  else if(path.endsWith('/dashboard/summary'))body={active_runs:2,waiting_approvals:2,succeeded_24h:0,failed_24h:1,recent_runs:runs,daily_trend:[]};
  else if(path.endsWith('/approvals'))body={items:[{approval_id:'sample-a'},{approval_id:'sample-b'}],total:2};
  else if(path.endsWith('/runs')){
    const source=url.searchParams.get('keyword')==='pagination' ? Array.from({length:101},(_,i)=>({...runs[i===100?3:0],run_id:`pagination-${String(i).padStart(3,'0')}`})) : runs;
    const visible=source.filter(run=>(!url.searchParams.get('status')||run.status===url.searchParams.get('status'))&&(!url.searchParams.get('keyword')||run.run_id.includes(url.searchParams.get('keyword'))));
    const page=Number(url.searchParams.get('page')||1),size=Number(url.searchParams.get('page_size')||12);
    body={items:visible.slice((page-1)*size,page*size),total:visible.length,page,page_size:size};
  }else if(path.includes('/runs/')){
    const id=decodeURIComponent(path.split('/').at(-1));const run=runs.find(r=>r.run_id===id)||runs[0];
    body={...run,config:{log_hours:24},steps:[],tasks:[{task_id:'UPS-0044',carrier:'UPS',result:'BLOCKED',lifecycle:{terminal_reason:'上游响应超时，需要人工检查验证环境。'}}],artifacts:[],run_lifecycle:{progress:{message:'验证完成，等待人工审阅',counts:{}}},actions:{pause:{enabled:false},resume:{enabled:false},retry:{enabled:false},cancel:{enabled:false}}};
  }
  await route.fulfill({json:body});
 });
 const overflow=()=>page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);
 try{
  await page.addInitScript(()=>localStorage.setItem('wb-theme','light'));
  await page.goto(origin+'/runs');await page.waitForLoadState('networkidle');
  await page.getByText('验证完成，等待人工审阅',{exact:true}).waitFor();
  const regions=await page.evaluate(()=>{
    const rect=s=>{const b=document.querySelector(s).getBoundingClientRect();return {x:b.x,y:b.y,width:b.width,height:b.height};};
    return {sidebar:rect('aside'),topbar:rect('header[class*="topbar"]'),heading:rect('h1'),summary:rect('[aria-label="批次统计"]'),table:rect('[aria-label="执行批次列表"]')};
  });
  assert.equal(regions.sidebar.width,216);assert.equal(regions.topbar.height,52);assert.equal(regions.heading.x,240);assert.equal(regions.heading.y,76);
  assert(regions.summary.height<=40,'Reference uses a compact strip, never three large KPI cards');
  assert(Math.abs(regions.summary.y-152.5)<2,'Summary aligns to the approved artboard');
  assert.equal(await page.getByRole('tablist',{name:'批次视图'}).getByRole('tab').count(),3);
  assert.equal(await page.locator('[aria-label="执行批次列表"] footer').count(),1);
  await page.screenshot({path:dir+'/runs-reference-size.png'});
  await page.getByRole('tab',{name:'已结束',exact:false}).click();
  assert.equal(await page.locator('tbody tr').filter({hasText:'RUN-1024-0830'}).count(),1);
  assert.equal(await page.locator('tbody tr').filter({hasText:'RUN-1023-1400'}).count(),1,'Ended includes failed batches');
  assert.equal(await page.locator('tbody tr').filter({hasText:'RUN-1025-0830'}).count(),0);
  await page.getByRole('tab',{name:'全部批次',exact:true}).click();

  await page.getByRole('button',{name:'收起批次 RUN-1025-0830',exact:true}).click();assert.equal(await page.getByText('最近动作',{exact:true}).count(),0);
  await page.getByRole('button',{name:'展开批次 RUN-1025-0700',exact:true}).click();await page.getByText('验证完成，等待人工审阅',{exact:true}).waitFor();
  assert(requests.some(u=>u.pathname.endsWith('/runs/RUN-1025-0700')),'Expanded content must load its actual run');
  await page.getByRole('button',{name:'更多筛选项',exact:true}).click();
  await page.getByRole('radio',{name:'失败',exact:true}).check();
  await page.waitForFunction(()=>[...document.querySelectorAll('tbody tr')].some(row=>row.textContent.includes('RUN-1023-1400')));
  assert.equal(await page.locator('tbody tr').filter({hasText:'RUN-1025-0830'}).count(),0,'Exact status filter excludes running batches');
  await page.getByRole('button',{name:'重置筛选',exact:true}).click();await page.mouse.click(450,110);
  await page.getByRole('button',{name:'批次时间范围',exact:true}).click();await page.getByRole('menuitem',{name:'最近 24 小时',exact:true}).click();await page.waitForLoadState('networkidle');
  const lastList=requests.filter(u=>u.pathname.endsWith('/runs')).at(-1);const age=Date.now()-Date.parse(lastList.searchParams.get('from'));
  assert(Math.abs(age-86400000)<10000,'Time selector reaches the supported API filter');
  await page.keyboard.press('Control+k');const modal=page.getByRole('dialog');await modal.getByRole('textbox',{name:'快捷搜索批次 ID',exact:true}).fill('1025-0700');
  await modal.getByText('RUN-1025-0700',{exact:true}).waitFor();await modal.getByText('RUN-1025-0700',{exact:true}).click();await page.waitForURL('**/runs/RUN-1025-0700');
  await page.goto(origin+'/runs');await page.waitForLoadState('networkidle');
  await page.getByRole('button',{name:'文档',exact:false}).click();await page.getByText('工作台使用说明',{exact:true}).waitFor();await page.keyboard.press('Control+k');assert.equal(await page.getByRole('dialog').count(),1,'Global search must not stack another modal over the help drawer');await page.locator('.ant-drawer-content').getByRole('button',{name:/Close|关闭/i}).click();
  await page.getByRole('button',{name:'待处理消息',exact:true}).click();await page.getByText('2 个本地候选等待交付审批。',{exact:true}).waitFor();await page.mouse.click(450,110);
  await page.getByRole('textbox',{name:'搜索批次 ID',exact:true}).fill('pagination');
  await page.getByRole('tab',{name:'已结束',exact:false}).click();
  await page.locator('tbody').getByText('pagination-100',{exact:true}).waitFor();
  assert(requests.some(u=>u.pathname.endsWith('/runs')&&u.searchParams.get('keyword')==='pagination'&&u.searchParams.get('page')==='2'),'Ended group reads later summary pages');
  assert.equal(await page.locator('tbody').getByText('pagination-000',{exact:true}).count(),0);
  for(const theme of ['light','dark']){
    await page.addInitScript(value=>localStorage.setItem('wb-theme',value),theme);
    for(const width of [1440,768,390]){
      await page.setViewportSize({width,height:1000});await page.goto(origin+'/runs');await page.waitForLoadState('networkidle');
      assert.equal(await overflow(),false,`${theme}/${width}: viewport overflow`);
      assert.equal(await page.locator('html').getAttribute('data-theme'),theme);
      await page.screenshot({path:`${dir}/runs-${theme}-${width}.png`,fullPage:true});
    }
  }
  assert.deepEqual(errors,[]);assert(methods.every(method=>method==='GET'));
  const report={result:'passed',scope:'Execution batch page and shared navigation fidelity correction',sourceArtboard:'7275588c35de440380eaadd20b198a78',referenceViewport:{width:1280,height:1024,deviceScaleFactor:2},regions,assertions:['compact summary matches reference hierarchy','216px sidebar / 52px header / 24px content inset','expandable real batch detail','pagination stays inside table surface','ended group includes terminal records beyond the first 100 summaries','active / ended groups and exact status filters preserve real batch states','time filter calls API','Ctrl+K searches and navigates actual batch IDs','documentation and pending approval controls work','light/dark at 1440/768/390','no page errors or writes'],dataPolicy:'Explicit isolated fixtures. No sample names or metrics written to production components.'};
  writeFileSync(dir+'/report.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report));
 }catch(error){await page.screenshot({path:dir+'/failure.png',fullPage:true});throw error;}
 finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1});
