import React, { ReactNode } from "react";
import { characterImage } from "../characterAssets";

export type DisplayJob={product_id:string;status:string;current_stage:string;progress:number;artifacts?:unknown[]};

export function CharacterAvatar({characterId,imageSrc=characterImage(characterId),fallbackLabel,status,size="medium"}:{characterId:string;imageSrc?:string;fallbackLabel:string;status?:string;size?:"small"|"medium"|"large"}){
  return <div className={`character-avatar ${size}`} data-character-id={characterId}>{imageSrc?<img src={imageSrc} alt={fallbackLabel}/>:<span aria-label={`${fallbackLabel} 이미지 준비 중`}>{fallbackLabel.slice(0,2)}</span>}{status&&<i title={status}/>}</div>
}
export function EmployeeCard({characterId,employeeName,departmentName,roleName,displayStatus="준비 중",imageSrc,action}:{characterId:string;employeeName:string;departmentName:string;roleName:string;displayStatus?:string;imageSrc?:string;action?:ReactNode}){
  return <article className="employee-card"><CharacterAvatar characterId={characterId} imageSrc={imageSrc} fallbackLabel={employeeName} status={displayStatus}/><div><small>{departmentName}</small><h3>{employeeName}</h3><p>{roleName}</p><span className="prepared-badge">{displayStatus}</span></div>{action}</article>
}
export function DepartmentPanel({name,description,employees=[],activeWork=0}:{name:string;description:string;employees?:ReactNode[];activeWork?:number}){
  return <section className="company-panel"><header><div><p className="eyebrow">DEPARTMENT</p><h2>{name}</h2><p>{description}</p></div><span>{activeWork?`진행 업무 ${activeWork}건`:"연결 준비 중"}</span></header><div className="employee-grid">{employees.length?employees:<p className="empty-copy">직원 데이터 연결 준비 중</p>}</div></section>
}
export function ActivityCharacter({stage}:{stage?:string}){const copy:Record<string,string>={PLANNING:"회의 준비",MUSIC:"음악 작업",IMAGE:"디자인 작업",VIDEO:"영상 작업",YOUTUBE:"업로드 작업",NAVER:"게시 작업"};return <div className="activity-character"><CharacterAvatar characterId="general-potato" fallbackLabel="PM" size="large"/><div><small>현재 활동</small><strong>{copy[stage||""]||"업무 대기"}</strong><p>캐릭터 애니메이션 연결 준비 중</p></div></div>}
export function CEOWelcome({companyName,currentJob}:{companyName:string;currentJob?:DisplayJob}){return <section className="ceo-welcome"><CharacterAvatar characterId="lim-potato" fallbackLabel="CEO" size="large"/><div><p className="eyebrow">POTATO COMPANY</p><h1>{companyName}에 오신 것을 환영합니다</h1><p>{currentJob?`${currentJob.current_stage} 단계 업무를 확인하고 있습니다.`:"새 업무를 시작할 준비가 되었습니다."}</p></div></section>}
export function CompanySummaryCards({jobs}:{jobs:DisplayJob[]}){const running=jobs.filter(j=>j.status==="RUNNING").length;const complete=jobs.filter(j=>j.status==="COMPLETED").length;const artifacts=jobs.reduce((sum,j)=>sum+(j.artifacts?.length||0),0);return <section className="summary-grid"><Summary label="진행 중 업무" value={`${running}건`}/><Summary label="완료 업무" value={`${complete}건`}/><Summary label="기록된 예상 비용" value="집계 준비 중" pending/><Summary label="총 결과물" value={`${artifacts}개`}/></section>}
function Summary({label,value,pending=false}:{label:string;value:string;pending?:boolean}){return <article><small>{label}</small><strong>{value}</strong>{pending&&<span className="prepared-badge">준비 중</span>}</article>}
export function CurrentWorkList({jobs,onOpen}:{jobs:DisplayJob[];onOpen:(job:DisplayJob)=>void}){return <section className="company-panel"><h2>현재 업무</h2>{jobs.length?<div className="work-rows">{jobs.slice(0,5).map(job=><button key={job.product_id} onClick={()=>onOpen(job)}><span>{job.current_stage}</span><strong>{job.progress}%</strong></button>)}</div>:<p className="empty-copy">현재 진행 중인 업무가 없습니다.</p>}</section>}
export const RecentWorkList=CurrentWorkList;
export function FutureFeaturePanel({title,description}:{title:string;description:string}){return <article className="future-card"><span className="prepared-badge">준비 중</span><h3>{title}</h3><p>{description}</p></article>}
