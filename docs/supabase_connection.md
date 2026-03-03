# 🐛 WSL2 Cannot Connect to Supabase — IPv6 Issue

> **Stack:** WSL2 Ubuntu 22.04 + FastAPI + Supabase (hosted Postgres)  
> **Symptom:** FastAPI cannot connect to Supabase database — timeouts and "network unreachable" errors  
> **Root Cause:** WSL2 fails to establish IPv6 connections, and Supabase's direct connection URL now resolves over IPv6  
> **Status:** ⚠️ Root WSL2 cause unresolved — workaround via Supabase connection pooler is stable

---

## The Problem

Our FastAPI backend running inside WSL2 could not connect to the Supabase-hosted Postgres database. The standard `DATABASE_URL` from the Supabase dashboard failed consistently with connection timeouts and network errors.

![WSL Connection Error](./img/er2.png)

---

## Environment

| Component | Version / Details |
|-----------|------------------|
| OS | WSL2 Ubuntu 22.04 |
| Backend | FastAPI (Python) |
| Database | Supabase hosted Postgres |
| ORM | SQLAlchemy |

---

## Why It Happened

The issue sits at the intersection of two separate problems:

**1. Supabase dropped direct IPv4 support**

Supabase's direct connection URL (the one shown under *Settings → Database → Connection String*) now resolves to an IPv6 address. Previously it supported IPv4 connections directly — this changed and the old-style direct connection URL no longer works over IPv4.

**2. WSL2 cannot connect over IPv6**

WSL2 runs inside a lightweight Hyper-V virtual machine with its own virtualized network adapter. IPv6 connectivity from inside WSL2 to external hosts is unreliable — connections hang or fail outright regardless of configuration. We tried several WSL2 network fixes but none resolved the IPv6 connectivity:

- Forcing `127.0.0.1` in the connection string — did not help since the Supabase host itself resolves to IPv6
- Modifying `/etc/resolv.conf` — no effect on the IPv6 routing issue
- WSL2 network mode settings — connection still failed

The root cause of WSL2's IPv6 failure is still **unidentified**. It may be related to the host Windows network configuration, Hyper-V virtual switch settings, or WSL2 kernel networking — but we have not been able to isolate it.

---

## What We Tried (That Failed)

| Attempt | Result |
|---------|--------|
| Using `127.0.0.1` instead of `localhost` in DATABASE_URL | ❌ Still fails — Supabase host resolves to IPv6 regardless |
| Modifying `/etc/resolv.conf` to prefer IPv4 DNS | ❌ No effect on outbound IPv6 routing |
| Various WSL2 `.wslconfig` network settings | ❌ IPv6 connections still timing out |
| Direct Postgres connection on port `5432` | ❌ Fails — URL resolves to IPv6 |

---

## The Workaround — Supabase Connection Pooler ✅

Instead of connecting directly to Postgres, we switched to the **Supabase connection pooler** (PgBouncer). The pooler URL uses a different hostname that resolves over IPv4, which WSL2 handles without issues.

![Supabase Connection Pooling](./img/er1.png)

### How to get the pooler URL

1. Go to your Supabase project dashboard
2. Navigate to **Settings → Database**
3. Scroll to **Connection Pooling**
4. Enable it and copy the **Connection string** (uses port `6543`)

### Update your `.env`

```bash
# ❌ Direct connection — resolves to IPv6, fails in WSL2
DATABASE_URL=postgresql://postgres:[PASSWORD]@db.xxxx.supabase.co:5432/postgres

# ✅ Pooler connection — resolves to IPv4, works in WSL2
DATABASE_URL=postgresql://postgres.[PROJECT-REF]:[PASSWORD]@aws-0-[REGION].pooler.supabase.com:6543/postgres
```

### SQLAlchemy config (FastAPI)

If using SQLAlchemy, add `?pgbouncer=true` to disable prepared statements which are incompatible with PgBouncer in transaction mode:

```python
# database.py
DATABASE_URL = os.getenv("DATABASE_URL")

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,       # verify connections before using them
    pool_recycle=300,          # recycle connections every 5 minutes
)
```

Or append the parameter directly in the URL:

```bash
DATABASE_URL=postgresql://postgres.[PROJECT-REF]:[PASSWORD]@aws-0-[REGION].pooler.supabase.com:6543/postgres?pgbouncer=true
```

**Result:** Stable connection. The pooler hostname resolves over IPv4 which WSL2 can reach without issues.

---

## Open Investigation

The underlying WSL2 IPv6 issue remains unresolved. If you are investigating further, these are the most likely areas:

- **Hyper-V virtual switch** — the WSL2 VM's virtual network adapter may not have a properly configured IPv6 default gateway
- **Windows host IPv6 routing** — if the Windows host has IPv6 misconfigured, WSL2 inherits the broken routing table
- **WSL2 kernel networking** — some WSL2 kernel versions have known IPv6 bugs; check `uname -r` and compare against known issues
- **MTU mismatch** — WSL2 sometimes has MTU issues that affect large packets over IPv6

To help diagnose when you revisit this:

```bash
# Check if IPv6 is reachable at all from WSL2
ping6 google.com

# Check your WSL2 network interfaces
ip addr show

# Check routing table
ip -6 route show

# Test connecting to Supabase direct host over IPv6
curl -v "https://db.xxxx.supabase.co:5432" 2>&1 | head -20
```

---

## Checklist

- [x] Switched from direct Postgres URL (port `5432`) to pooler URL (port `6543`)
- [x] Connection pooling enabled in Supabase dashboard
- [ ] Root cause of WSL2 IPv6 failure identified *(still open)*
- [ ] Verified behaviour on a fresh WSL2 instance

---

## Key Takeaway

> Supabase's direct connection URL now resolves to IPv6. WSL2 cannot connect over IPv6 for reasons we haven't fully identified. The Supabase connection pooler provides a stable IPv4-based workaround and is the recommended connection method for WSL2 development environments.