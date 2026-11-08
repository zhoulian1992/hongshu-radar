// ==UserScript==
// @name         红薯雷达 · 加入监控队列
// @namespace    local.hongshu.radar
// @version      1.0
// @description  在小红书商品页点一下，把商品链接/当前销量推给本机红薯雷达（对应文章最后的插件思路）
// @match        https://www.xiaohongshu.com/goods-detail/*
// @match        https://mall.xiaohongshu.com/*
// @grant        GM_xmlhttpRequest
// @connect      127.0.0.1
// ==/UserScript==

(function () {
  'use strict';
  const ENDPOINT = 'http://127.0.0.1:8777/api/ingest';

  function pickText(selectors) {
    for (const sel of selectors) {
      const el = document.querySelector(sel);
      if (el && el.textContent.trim()) return el.textContent.trim();
    }
    return null;
  }

  // 页面结构会变，找不到就只推链接（加入队列），不猜销量
  function readSold() {
    const text = document.body.innerText || '';
    const m = text.match(/(已售\s*[0-9.]+\s*[万wW]?\+?|[0-9.]+\s*万\+?\s*人已购)/i);
    return m ? m[0] : null;
  }

  function push(withSold) {
    const body = {
      url: location.href,
      title: document.title.replace(/[-|]\s*小红书.*$/, '').trim() || null,
      source: 'plugin',
    };
    if (withSold) {
      const sold = readSold();
      if (sold) body.sold = sold;
      body.price = pickText(['.price', '[class*="price"]']);
      body.price = body.price ? parseFloat(body.price.replace(/[^\d.]/g, '')) : null;
    }
    GM_xmlhttpRequest({
      method: 'POST',
      url: ENDPOINT,
      headers: { 'Content-Type': 'application/json' },
      data: JSON.stringify(body),
      onload: (res) => {
        let msg = '已提交';
        try { const j = JSON.parse(res.responseText); msg = j.action === 'snapshot' ? '已写入快照' : '已加入监控队列'; } catch (e) { }
        flash('✓ ' + msg + (res.status === 200 ? '' : ` (${res.status})`));
      },
      onerror: () => flash('✗ 连不上本机服务，先运行 python3 app.py'),
    });
  }

  function flash(text) {
    let tip = document.getElementById('radar-tip');
    if (!tip) {
      tip = document.createElement('div');
      tip.id = 'radar-tip';
      tip.style.cssText = 'position:fixed;right:24px;bottom:96px;z-index:99999;background:#252a33;color:#fff;padding:10px 16px;border-radius:8px;font-size:13px;box-shadow:0 8px 24px rgba(0,0,0,.2)';
      document.body.appendChild(tip);
    }
    tip.textContent = text;
    setTimeout(() => tip.remove(), 2600);
  }

  function makeButton(label, handler) {
    const b = document.createElement('button');
    b.textContent = label;
    b.style.cssText = 'position:fixed;right:24px;z-index:99998;background:#ff2442;color:#fff;border:0;border-radius:8px;padding:9px 14px;font-size:13px;cursor:pointer;box-shadow:0 4px 14px rgba(255,36,66,.3)';
    b.onclick = handler;
    document.body.appendChild(b);
    return b;
  }

  const b1 = makeButton('加入监控队列', () => push(false));
  b1.style.bottom = '144px';
  const b2 = makeButton('采集当前销量', () => push(true));
  b2.style.bottom = '96px';
  b2.style.background = '#0f9d76';
})();
