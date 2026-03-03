# Auth Cookies Not Sent on Subsequent Requests

> **Stack:** React (Vite) + FastAPI + Supabase OAuth  
> **Symptom:** Token exchange returns 200, but every `/auth/me` call returns 401  
> **Root Cause:** Cross-origin cookie blocking due to mismatched ports in local development

---

## The Problem

Our setup ran two servers locally:

| Service  | Address |
|----------|---------|
| Frontend | `localhost:8080` (Vite) |
| Backend  | `localhost:8000` (FastAPI) |

After a successful OAuth token exchange, the backend set an `access_token` cookie. Every subsequent call to `/api/v1/auth/me` returned **401 Unauthorized** — the backend kept reporting that no `access_token` cookie was present, even though it was visible in the browser.

(./img/er3.png)

The backend logs confirmed it clearly:

```
=== TOKEN EXTRACTION DEBUG ===
Request cookies: {'cookie_consent': 'true', '_ga': '...', '_clck': '...'}
Token from cookie: None
=== END TOKEN EXTRACTION DEBUG ===
401 - No token provided
```

Only analytics cookies were arriving — never the `access_token`.

---

## Why It Happened

Browsers enforce the **Same-Origin Policy**: two URLs are considered different origins if they differ in protocol, domain, **or port**. Even though both servers were on `localhost`, port `8080 ≠ 8000`, so they were treated as completely separate origins.

When the exchange request was made **directly to `localhost:8000`**:

```
Browser (localhost:8080) ──direct call──▶ localhost:8000
                                          ✅ 200 OK, cookie set on :8000
```

The browser stored the cookie under the `:8000` origin. When the frontend then called `/auth/me` (routed through `:8080`), the browser found no cookie for that origin and sent nothing:

```
Browser (localhost:8080) ──▶ localhost:8080/api/auth/me
                              ❌ No cookie — cookie belongs to :8000, not :8080
```

---

## Why We Didn't Notice It Sooner

Several things masked the real cause:

1. **The exchange returned 200** — everything looked fine on the surface.
2. **The cookie was visible in DevTools** — it appeared in the browser's cookie store, just under the wrong origin (`:8000`).
3. **We chased the wrong fixes first** — we spent time adjusting `SameSite`, `Secure`, and `httponly` settings, none of which were the actual problem.
4. **The requests went to `:8080` in the console** — once the proxy was partially set up, `/auth/me` correctly routed through Vite. But the exchange call still had a hardcoded fallback (`|| 'http://localhost:8000'`) that bypassed the proxy silently.
5. **The hardcoded fallback was invisible** — `VITE_API_URL` was set to empty, so the `|| 'http://localhost:8000'` fallback kicked in without any warning.

---

## The Fix

### 1. Configure a Vite Proxy

The proxy makes the browser believe both the frontend and backend are on the same origin. All `/api` requests are intercepted by Vite and forwarded to the backend server-side — outside the browser, where the Same-Origin Policy doesn't apply.

```typescript
// vite.config.ts
export default defineConfig({
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        secure: false,
        cookieDomainRewrite: 'localhost',
      }
    }
  }
})
```

### 2. Use Relative URLs Everywhere

All frontend API calls must use relative paths so they go through the proxy:

```typescript
// ❌ Before — bypasses proxy, cookie set on wrong origin
fetch(`http://localhost:8000/api/v1/auth/oauth/exchange`, ...)
fetch(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/v1/auth/me`, ...)

// ✅ After — goes through proxy, cookie scoped to :8080
fetch(`/api/v1/auth/oauth/exchange`, ...)
fetch(`/api/v1/auth/me`, ...)
```

### 3. Clear the env variable

```bash
# .env
VITE_API_URL=
```

And remove any hardcoded fallback URLs in code:

```typescript
// ❌ This fallback silently bypasses the proxy
const url = `${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/...`

// ✅ Just use a relative path
const url = `/api/...`
```

---

## How the Proxy Works

```
Without proxy:
  Browser → localhost:8000/api/exchange  (direct, cross-origin)
            cookie stored on :8000
  Browser → localhost:8080/api/me        (no cookie — wrong origin)
            ❌ 401

With proxy:
  Browser → localhost:8080/api/exchange  → Vite → localhost:8000
            cookie stored on :8080
  Browser → localhost:8080/api/me        → Vite → localhost:8000
            ✅ cookie sent, 200 OK
```

The key insight: the proxy moves the `:8000` call **outside the browser** entirely. The browser only ever talks to `:8080`, so all cookies are scoped to one origin.

---

## Production

In production this issue disappears because the frontend and backend are served from the **same domain**:

```
Frontend  →  https://myapp.com
Backend   →  https://myapp.com/api/...
```

No proxy needed — same origin, cookies work automatically. Just update:

**Backend CORS:**
```python
origins = [
    "http://localhost:8080",   # dev
    "https://myapp.com",       # production
]
```

**Backend cookie settings:**
```python
is_production = os.getenv("ENVIRONMENT") == "production"

cookie_settings = {
    "httponly": True,
    "secure": is_production,     # True in prod (requires HTTPS)
    "samesite": "lax",
    "max_age": 2592000,
    "path": "/",
}
```

Your frontend fetch calls need **no changes** — `/api/...` relative paths work in both environments.

---

## Checklist

Use this when debugging auth cookie issues in future:

- [ ] Are both requests (exchange + me) going through the same origin in the Network tab?
- [ ] Is there a `Set-Cookie` header on the exchange response?
- [ ] Is there a `Cookie: access_token=...` header on the `/me` request?
- [ ] Are there any hardcoded `http://localhost:PORT` URLs in the codebase? (`grep -r "localhost:8000" src/`)
- [ ] Is `credentials: "include"` on every fetch call?
- [ ] Was Vite restarted after proxy/env changes?

---

## Key Takeaway

> If your cookie is visible in DevTools but not arriving at the server — check which **origin** it belongs to. A cookie set on `:8000` will never be sent to `:8080`, even on the same machine.