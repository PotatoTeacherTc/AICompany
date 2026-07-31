# AICompany Web Dashboard

```powershell
npm.cmd install
$env:VITE_API_BASE_URL="http://127.0.0.1:8000"
npm.cmd run dev
```

The access token is held in memory only and is cleared on logout or refresh.
The dashboard uses authenticated Backend APIs. It does not implement Billing,
Subscription, Worker creation, WebSocket streaming, or external provider calls.
