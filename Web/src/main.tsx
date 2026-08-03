import React, { FormEvent, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { api, ApiError } from "./api";
import "./styles.css";
import "./product.css";

type Workspace = { workspace_id: string; name: string };
type Stage = { status: string; safe_error?: string };
type Artifact = { artifact_id?: string; artifact_type?: string; filename?: string; mime_type?: string };
type ProductJob = {
  product_id: string; job_id?: string; status: string; progress: number;
  current_stage: string; stages: Record<string, Stage>; artifacts: Artifact[];
  results: Record<string, unknown>; safe_error?: string;
};
type Connection = { component: string; status: string };
type ArtifactContent = { status: string; artifact_id: string; mime_type?: string; content: unknown };
const navigation = ["Create", "Work", "Results", "Connections"];
const stages = ["PLANNING", "MUSIC", "IMAGE", "BLOG", "VIDEO", "YOUTUBE", "NAVER"];

function App() {
  const [token, setToken] = useState("");
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspace, setWorkspace] = useState("");
  const [page, setPage] = useState("Create");
  const [jobs, setJobs] = useState<ProductJob[]>([]);
  const [selected, setSelected] = useState<ProductJob | null>(null);
  const [connections, setConnections] = useState<Connection[]>([]);
  const [state, setState] = useState<"idle"|"loading"|"error"|"forbidden">("idle");
  const [message, setMessage] = useState("");
  const [artifactView, setArtifactView] = useState<{name:string; content:string}|null>(null);

  async function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const data = new FormData(event.currentTarget); setState("loading");
    try {
      const result = await api<{access_token:string}>("/auth/login", undefined, {method:"POST", body:JSON.stringify({email:data.get("email"),password:data.get("password")})});
      const values = await api<{items:Workspace[]}>("/workspaces", result.access_token);
      setToken(result.access_token); setWorkspaces(values.items || []); setWorkspace(values.items?.[0]?.workspace_id || ""); setState("idle");
    } catch (error) { fail(error, "Login failed"); }
  }

  async function refresh() {
    if (!token || !workspace) return;
    try {
      const [work, links] = await Promise.all([
        api<{items:ProductJob[]}>(`/workspaces/${workspace}/product-jobs`, token),
        api<{items:Connection[]}>(`/workspaces/${workspace}/connections`, token),
      ]);
      setJobs(work.items || []); setConnections(links.items || []);
      if (selected) setSelected(work.items.find(item=>item.product_id===selected.product_id) || null);
      setState("idle");
    } catch (error) { fail(error, "Unable to load product state"); }
  }

  useEffect(() => { refresh(); const timer=window.setInterval(refresh, 2000); return()=>window.clearInterval(timer); }, [token, workspace]);

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form=new FormData(event.currentTarget); const request=String(form.get("request")||"").trim(); if(!request)return;
    setState("loading");
    try {
      const value=await api<ProductJob>(`/workspaces/${workspace}/product-jobs`,token,{method:"POST",body:JSON.stringify({request,idempotency_key:crypto.randomUUID()})});
      setSelected(value); setPage("Work"); (event.currentTarget as HTMLFormElement).reset(); await refresh();
    } catch(error){fail(error,"Unable to start work");}
  }

  async function retry(job: ProductJob) {
    try { await api(`/workspaces/${workspace}/product-jobs/${job.product_id}/retry`,token,{method:"POST",body:JSON.stringify({stage:job.current_stage})}); await refresh(); }
    catch(error){fail(error,"Unable to retry stage");}
  }
  async function uploadAudio(job:ProductJob,file:File){
    const filename=encodeURIComponent(file.name);
    try{await api(`/workspaces/${workspace}/product-jobs/${job.product_id}/audio?filename=${filename}`,token,{method:"POST",body:file,headers:{"Content-Type":file.type||"application/octet-stream"}},300000);await refresh();}
    catch(error){fail(error,"Audio upload was rejected");}
  }
  async function resume(job:ProductJob){try{await api(`/workspaces/${workspace}/product-jobs/${job.product_id}/resume`,token,{method:"POST"});await refresh();}catch(error){fail(error,"Checkpoint is not ready");}}
  async function openArtifact(item:Artifact){
    if(!item.artifact_id)return;
    try{
      const value=await api<ArtifactContent>(`/workspaces/${workspace}/artifacts/${item.artifact_id}/content`,token);
      const content=typeof value.content==="string"?value.content:JSON.stringify(value.content,null,2);
      setArtifactView({name:item.filename||item.artifact_type||"Artifact",content});
    }catch(error){fail(error,"This artifact is available as managed media, not inline text");}
  }

  function fail(error:unknown,fallback:string){setState(error instanceof ApiError&&error.status===403?"forbidden":"error");setMessage(error instanceof Error?error.message:fallback);}
  if(!token)return <main className="login"><section><p className="eyebrow">PROJECT APEX</p><h1>Your creative company,<br/>in one place.</h1><p className="muted">Sign in to start and follow a complete content project.</p><form onSubmit={login}><label>Email<input name="email" type="email" required/></label><label>Password<input name="password" type="password" required/></label><button disabled={state==="loading"}>{state==="loading"?"Signing in…":"Enter workspace"}</button>{state==="error"&&<p role="alert" className="error">{message}</p>}</form></section></main>;

  return <div className="shell"><aside><div><p className="eyebrow">AICompany</p><h2>Studio</h2></div><nav aria-label="Primary">{navigation.map(item=><button className={page===item?"active":""} onClick={()=>setPage(item)} key={item}>{item}</button>)}</nav><button className="logout" onClick={()=>{setToken("");setJobs([])}}>Log out</button></aside><main className="content"><header><div><p className="eyebrow">LOCAL PRODUCT MODE</p><h1>{page}</h1></div><label className="workspace">Workspace<select value={workspace} onChange={e=>{setWorkspace(e.target.value);setSelected(null)}}>{workspaces.map(item=><option value={item.workspace_id} key={item.workspace_id}>{item.name}</option>)}</select></label></header>{state==="forbidden"?<State text="You do not have access to this workspace."/>:state==="error"?<State text={message}/>:page==="Create"?<Create onSubmit={create}/>:page==="Connections"?<Connections items={connections}/>:page==="Results"?<Results jobs={jobs} select={job=>{setSelected(job);setPage("Work")}}/>:<Work jobs={jobs} selected={selected} setSelected={setSelected} retry={retry} uploadAudio={uploadAudio} resume={resume} openArtifact={openArtifact}/>}</main>{artifactView&&<dialog open className="artifact-view"><h3>{artifactView.name}</h3><pre>{artifactView.content}</pre><button onClick={()=>setArtifactView(null)}>Close</button></dialog>}</div>;
}

function Create({onSubmit}:{onSubmit:(event:FormEvent<HTMLFormElement>)=>void}){return <section className="composer"><p className="eyebrow">ONE REQUEST</p><h2>What should your AICompany make?</h2><form onSubmit={onSubmit}><textarea name="request" rows={6} maxLength={4000} required placeholder="감성 피아노곡을 만들어서 유튜브 비공개 업로드하고 네이버 게시 준비까지 해줘."/><button>Start project</button></form><p className="muted">Manual audio intake and final publication confirmation remain visible safety checkpoints.</p></section>}
function Work({jobs,selected,setSelected,retry,uploadAudio,resume,openArtifact}:{jobs:ProductJob[];selected:ProductJob|null;setSelected:(job:ProductJob)=>void;retry:(job:ProductJob)=>void;uploadAudio:(job:ProductJob,file:File)=>void;resume:(job:ProductJob)=>void;openArtifact:(item:Artifact)=>void}){const job=selected||jobs[0];return <><section className="job-list"><h3>Projects</h3>{jobs.length?jobs.map(item=><button key={item.product_id} onClick={()=>setSelected(item)}><span>{item.current_stage}</span><b>{item.status}</b></button>):<p className="muted">No work started yet.</p>}</section>{job&&<section className="workflow"><div className="progress"><span style={{width:`${job.progress}%`}}/></div><p>{job.progress}% · {job.status}</p><div className="stage-grid">{stages.map(name=><article key={name} className={job.stages[name]?.status.toLowerCase()}><small>{name}</small><strong>{job.stages[name]?.status||"PENDING"}</strong></article>)}</div>{job.status==="FAILED"&&<button onClick={()=>retry(job)}>Retry {job.current_stage}</button>}{job.status==="WAITING_FOR_INPUT"&&job.current_stage==="PLANNING"&&<section className="checkpoint"><h3>Suno audio required</h3><p>Review the generated Suno package below, create the track manually, then upload the completed audio.</p><ArtifactList items={job.artifacts} openArtifact={openArtifact} planningOnly/><input aria-label="Completed audio" type="file" accept=".mp3,.wav,.flac,.m4a,audio/*" onChange={e=>{const file=e.target.files?.[0];if(file)uploadAudio(job,file)}}/></section>}{["CONNECTION_REQUIRED","USER_ACTION_REQUIRED","USER_CONFIRM_REQUIRED"].includes(job.status)&&<section className="checkpoint"><p>{job.current_stage==="NAVER"?"네이버 브라우저에서 최종 게시를 확인한 뒤 AICompany가 게시 URL을 회수합니다.":"Complete the indicated account action, then continue."}</p><button onClick={()=>resume(job)}>{job.current_stage==="NAVER"?"네이버 게시 준비 열기":"Continue"}</button></section>}<ResultDetail job={job} openArtifact={openArtifact}/></section>}</>}
function Results({jobs,select}:{jobs:ProductJob[];select:(job:ProductJob)=>void}){return <section className="panel"><h3>Project results</h3>{jobs.filter(job=>Object.keys(job.results||{}).length||job.artifacts?.length).map(job=><button className="result-row" key={job.product_id} onClick={()=>select(job)}><span>{job.product_id}</span><b>{job.artifacts?.length||0} artifacts</b></button>)}</section>}
function ResultDetail({job,openArtifact}:{job:ProductJob;openArtifact:(item:Artifact)=>void}){return <section className="panel results"><h3>Results</h3><div className="result-grid">{["music","image","blog","video","youtube","naver"].map(kind=>{const value=job.results?.[kind] as {published_url?:unknown}|undefined;const url=typeof value?.published_url==="string"&&/^https:\/\/(www\.youtube\.com|blog\.naver\.com)\//.test(value.published_url)?value.published_url:null;return <article key={kind}><small>{kind}</small><strong>{value?"Available":"Pending"}</strong>{url&&<a href={url} target="_blank" rel="noreferrer">Open result</a>}</article>})}</div><ArtifactList items={job.artifacts} openArtifact={openArtifact}/><p className="muted">Managed media previews list safe names and metadata; internal paths stay hidden.</p></section>}
function ArtifactList({items,openArtifact,planningOnly=false}:{items:Artifact[];openArtifact:(item:Artifact)=>void;planningOnly?:boolean}){const filtered=(items||[]).filter((item,index,all)=>item.artifact_id&&all.findIndex(value=>value.artifact_id===item.artifact_id)===index).filter(item=>!planningOnly||["MUSIC_PLAN","SUNO_PACKAGE","TEXT","JSON"].includes(item.artifact_type||""));return filtered.length?<div className="artifact-list">{filtered.map(item=><button key={item.artifact_id} onClick={()=>openArtifact(item)}><span>{item.filename||item.artifact_type||"Artifact"}</span><small>View safe content</small></button>)}</div>:null}
function Connections({items}:{items:Connection[]}){return <section className="connection-grid">{items.map(item=><article key={item.component}><span className={`dot ${item.status.toLowerCase()}`}/><div><h3>{item.component}</h3><p>{item.status}</p></div></article>)}</section>}
function State({text}:{text:string}){return <section className="state" role="status">{text}</section>}
createRoot(document.getElementById("root")!).render(<React.StrictMode><App/></React.StrictMode>);
