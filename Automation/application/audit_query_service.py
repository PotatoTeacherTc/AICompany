import base64, json

class AuditQueryService:
    def __init__(self,audit_service): self.audit_service=audit_service
    def query(self,workspace_id,cursor=None,limit=50,offset=0,**filters):
        if not isinstance(limit,int) or limit<0 or not isinstance(offset,int) or offset<0: raise ValueError('invalid_pagination')
        items=self.audit_service.query(workspace_id,limit=None,offset=0,**filters)
        if cursor:
            try:
                marker=json.loads(base64.urlsafe_b64decode(cursor+'='*(-len(cursor)%4)))
                index=next(i for i,e in enumerate(items) if e['id']==marker['id'] and e['created_at']==marker['created_at'])+1
            except Exception: raise ValueError('invalid_cursor')
        else:index=offset
        page=items[index:index+limit]
        next_cursor=None
        if index+limit<len(items) and page:
            last=page[-1]; next_cursor=base64.urlsafe_b64encode(json.dumps({'id':last['id'],'created_at':last['created_at']}).encode()).decode().rstrip('=')
        return {'items':page,'total':len(items),'next_cursor':next_cursor,'limit':limit}
