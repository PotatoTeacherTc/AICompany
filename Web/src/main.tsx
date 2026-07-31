import React, { FormEvent, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { api, ApiError } from "./api";
import "./styles.css";

type Workspace = { workspace_id: string; name: string };
type Dashboard = Record<string, any>;
const baseNav = ["Overview", "Jobs", "Executions", "Artifacts", "Organization", "Usage & Plan"];

function App() {
  const [token, setToken] = useState("");
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspace, setWorkspace] = useState("");
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [page, setPage] = useState(baseNav[0]);
  const [state, setState] = useState<"idle"|"loading"|"error"|"forbidden">("idle");
  const [message, setMessage] = useState("");
  const [platformAdmin, setPlatformAdmin] = useState(false);
  const nav = platformAdmin ? [...baseNav, "Admin"] : baseNav;

  async function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setState("loading");
    try {
      const result = await api<{access_token:string}>("/auth/login", undefined, {
        method: "POST", body: JSON.stringify({email:data.get("email"), password:data.get("password")})
      });
      setToken(result.access_token);
      const values = await api<{items:Workspace[]}>("/workspaces", result.access_token);
      setWorkspaces(values.items || []);
      setWorkspace(values.items?.[0]?.workspace_id || "");
      try {
        const admin = await api<{platform_admin:boolean}>("/admin/me", result.access_token);
        setPlatformAdmin(admin.platform_admin === true);
      } catch { setPlatformAdmin(false); }
      setState("idle");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Login failed"); setState("error"); }
  }

  useEffect(() => {
    if (!token || !workspace) return;
    setState("loading");
    api<Dashboard>(`/workspaces/${workspace}/dashboard`, token)
      .then(value => { setDashboard(value); setState("idle"); })
      .catch(error => {
        setState(error instanceof ApiError && error.status === 403 ? "forbidden" : "error");
        setMessage(error instanceof Error ? error.message : "Unable to load dashboard");
      });
  }, [token, workspace]);

  if (!token) return <main className="login"><section><p className="eyebrow">PROJECT APEX</p><h1>Your AI company,<br/>under control.</h1><p className="muted">A private command surface for missions, artifacts and AI departments.</p><form onSubmit={login}><label>Email<input name="email" type="email" required /></label><label>Password<input name="password" type="password" required /></label><button disabled={state==="loading"}>{state==="loading"?"Signing in…":"Enter workspace"}</button>{state==="error"&&<p role="alert" className="error">{message}</p>}</form></section></main>;

  const jobs = dashboard?.jobs?.counts || {};
  const artifacts = dashboard?.artifacts?.counts || {};
  return <div className="shell"><aside><div><p className="eyebrow">AICompany</p><h2>Command</h2></div><nav aria-label="Primary">{nav.map(item=><button className={page===item?"active":""} onClick={()=>setPage(item)} key={item}>{item}</button>)}</nav><button className="logout" onClick={()=>{setToken("");setDashboard(null)}}>Log out</button></aside><main className="content"><header><div><p className="eyebrow">WORKSPACE OVERVIEW</p><h1>{page}</h1></div><label className="workspace">Workspace<select value={workspace} onChange={e=>setWorkspace(e.target.value)}>{workspaces.map(item=><option value={item.workspace_id} key={item.workspace_id}>{item.name}</option>)}</select></label></header>{state==="loading"?<State text="Loading workspace…" />:state==="forbidden"?<State text="You do not have access to this workspace." />:state==="error"?<State text={message}/>:!dashboard?<State text="No dashboard data yet."/>:<><section className="metrics"><Metric label="Plan" value={dashboard.plan?.name||"—"}/><Metric label="Jobs" value={jobs.total??0}/><Metric label="Active artifacts" value={artifacts.available??0}/><Metric label="Tokens" value={dashboard.usage?.total_tokens??0}/></section><section className="grid"><Panel title="Job health"><Bars values={jobs}/></Panel><Panel title="Recent jobs"><List items={dashboard.jobs?.recent} id="job_id"/></Panel><Panel title="Artifacts"><Bars values={artifacts}/></Panel><Panel title="Organization"><p className="large">{dashboard.organization?.department_count??0}</p><p className="muted">departments · {dashboard.organization?.worker_capability_count??0} worker capabilities</p></Panel></section></>}</main></div>;
}
function Metric({label,value}:{label:string,value:string|number}){return <article className="metric"><span>{label}</span><strong>{value}</strong></article>}
function Panel({title,children}:{title:string,children:React.ReactNode}){return <article className="panel"><h3>{title}</h3>{children}</article>}
function Bars({values}:{values:Record<string,number>}){return <div>{Object.entries(values).filter(([k])=>k!=="total").map(([k,v])=><div className="bar" key={k}><span>{k}</span><b>{v}</b></div>)}</div>}
function List({items=[],id}:{items:any[],id:string}){return items.length?<ul>{items.map(item=><li key={item[id]}><span>{item[id]}</span><b className={`status ${String(item.status).toLowerCase()}`}>{item.status}</b></li>)}</ul>:<p className="muted">Nothing here yet.</p>}
function State({text}:{text:string}){return <section className="state" role="status">{text}</section>}
createRoot(document.getElementById("root")!).render(<React.StrictMode><App/></React.StrictMode>);
