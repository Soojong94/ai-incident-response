// Scalar 호환 CORS proxy.
// Scalar 의 proxyUrl 호출 패턴을 정확히 모를 수 있어 가능한 3가지를 모두 수용.

const express = require('express');
const fetch = require('node-fetch');

const app = express();
app.use(express.raw({ type: '*/*', limit: '10mb' }));

app.use((req, res, next) => {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET,POST,PUT,DELETE,PATCH,OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', '*');
  res.setHeader('Access-Control-Expose-Headers', '*');
  if (req.method === 'OPTIONS') return res.sendStatus(204);
  next();
});

app.use(async (req, res) => {
  // (1) query param: ?scalar_url=... / ?url=...
  // (2) header:      x-scalar-proxy-url / x-proxy-url
  // (3) path:        /https://target/...   (cors-anywhere 호환)
  let target =
    req.query.scalar_url ||
    req.query.url ||
    req.headers['x-scalar-proxy-url'] ||
    req.headers['x-proxy-url'];
  if (!target) {
    const after = req.url.slice(1);
    if (after.startsWith('http')) target = after;
  }
  if (!target) {
    return res.status(400).json({ error: 'missing target url (expected query scalar_url= or path /http... )' });
  }

  try {
    const hdrs = { ...req.headers };
    delete hdrs.host;
    delete hdrs['content-length'];
    delete hdrs.connection;
    delete hdrs['x-scalar-proxy-url'];
    delete hdrs['x-proxy-url'];

    const isBodyless = ['GET', 'HEAD'].includes(req.method);
    const upstream = await fetch(target, {
      method: req.method,
      headers: hdrs,
      body: isBodyless ? undefined : req.body,
      redirect: 'follow',
    });

    res.status(upstream.status);
    upstream.headers.forEach((v, k) => {
      // CORS / hop-by-hop 헤더 충돌 방지
      if (k.toLowerCase() === 'access-control-allow-origin') return;
      res.setHeader(k, v);
    });
    const buf = await upstream.arrayBuffer();
    res.end(Buffer.from(buf));
  } catch (e) {
    res.status(502).json({ error: String(e && e.message || e) });
  }
});

const PORT = process.env.PORT || 8090;
app.listen(PORT, '0.0.0.0', () => console.log(`scalar-compat cors proxy on ${PORT}`));
