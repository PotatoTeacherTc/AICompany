# AICompany Web Dashboard

```powershell
npm.cmd install
$env:VITE_API_BASE_URL="http://127.0.0.1:8000"
npm.cmd run dev
```

The access token is held in memory only and is cleared on logout or refresh.
The dashboard uses authenticated Backend APIs. Platform Admin navigation is
shown only after `/admin/me` authorizes the current User. Subscription,
Manual/Fake Billing, and bounded Admin operations are Backend contracts; this
initial UI remains an overview surface rather than a full management console.
It does not implement real payment, Worker creation, WebSocket streaming, or
external provider calls.

Production build:

```powershell
npm.cmd run build
```

Only the loopback API base URL belongs in `.env.example`; access/refresh tokens,
passwords, provider keys, and other secrets must never be placed there.
