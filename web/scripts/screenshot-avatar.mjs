// Node 22+와 Chrome만 사용한다. Vite 시작·종료와 임시 Chrome 프로필 정리를 포함한다.
// node web/scripts/screenshot-avatar.mjs /workspace/agenthan-avatar/screenshots
// 수동 확인: cd web && npm run dev -- --host 127.0.0.1 → /avatar-preview.html?state=thinking
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";

const webRoot = fileURLToPath(new URL("../", import.meta.url));
const output = resolve(process.argv[2] ?? join(webRoot, "avatar-screenshots"));
const states = ["idle", "listening", "thinking", "speaking", "attention"];
const profile = await mkdtemp(join(webRoot, ".avatar-chrome-"));
let server;
let chrome;
let socket;

try {
  server = await createServer({ root: webRoot, server: { host: "127.0.0.1", port: 0, hmr: false } });
  await server.listen();
  const url = `http://127.0.0.1:${server.httpServer.address().port}/avatar-preview.html`;
  chrome = spawn(process.env.CHROME_BIN ?? "google-chrome", [
    "--headless=new", "--no-sandbox", "--disable-dev-shm-usage", "--no-first-run",
    "--remote-debugging-port=0", `--user-data-dir=${profile}`, "about:blank",
  ], { stdio: ["ignore", "ignore", "pipe"] });
  const browserUrl = await new Promise((resolveUrl, reject) => {
    let stderr = "";
    const timeout = setTimeout(() => reject(new Error(`Chrome 시작 실패: ${stderr}`)), 15_000);
    chrome.once("error", (error) => { clearTimeout(timeout); reject(error); });
    chrome.once("exit", (code) => { clearTimeout(timeout); reject(new Error(`Chrome exit ${code}: ${stderr}`)); });
    chrome.stderr.on("data", (data) => {
      stderr += data;
      const match = stderr.match(/DevTools listening on (ws:\/\/[^\s]+)/);
      if (match) { clearTimeout(timeout); resolveUrl(match[1]); }
    });
  });
  const target = await fetch(`http://${new URL(browserUrl).host}/json/new?about:blank`, { method: "PUT" }).then((r) => r.json());
  socket = new WebSocket(target.webSocketDebuggerUrl);
  await once(socket, "open");
  let sequence = 0;
  const waiting = new Map();
  const errors = [];
  const mediaRequests = [];
  socket.addEventListener("message", ({ data }) => {
    const message = JSON.parse(data);
    if (message.method === "Runtime.exceptionThrown") errors.push(message.params.exceptionDetails);
    if (message.method === "Network.requestWillBeSent" && /\/avatar\/.*\.(webm|mp4)(?:\?|$)/.test(message.params.request.url)) {
      mediaRequests.push(message.params.request.url);
    }
    const pending = waiting.get(message.id);
    if (!pending) return;
    waiting.delete(message.id);
    if (message.error) pending.reject(new Error(JSON.stringify(message.error)));
    else pending.resolve(message.result);
  });
  const command = (method, params = {}) => new Promise((resolveCommand, reject) => {
    const id = ++sequence;
    const timeout = setTimeout(() => { waiting.delete(id); reject(new Error(`${method} timed out`)); }, 15_000);
    waiting.set(id, {
      resolve: (result) => { clearTimeout(timeout); resolveCommand(result); },
      reject: (error) => { clearTimeout(timeout); reject(error); },
    });
    socket.send(JSON.stringify({ id, method, params }));
  });
  const evaluate = async (expression) => {
    const result = await command("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  const waitFor = async (expression) => {
    const deadline = Date.now() + 15_000;
    while (!(await evaluate(expression))) {
      if (Date.now() > deadline) throw new Error(`Browser condition timed out: ${expression}`);
      await delay(50);
    }
  };
  const click = (state) => evaluate(`document.querySelector('[data-avatar-state="${state}"]').click()`);
  await command("Page.enable");
  await command("Runtime.enable");
  await command("Network.enable");
  await command("Emulation.setDeviceMetricsOverride", { width: 560, height: 360, deviceScaleFactor: 2, mobile: false });
  await command("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-motion", value: "no-preference" }] });
  await command("Page.navigate", { url });
  await waitFor("document.querySelector('[data-avatar-state=idle]') !== null");
  await mkdir(output, { recursive: true });

  for (const state of states) {
    await click(state);
    await waitFor(`(() => {
      const avatar = document.querySelector('.agent-desk');
      const video = avatar.querySelector('video');
      return avatar.dataset.state === '${state}' && avatar.querySelectorAll('video').length === 1
        && video.readyState >= 2 && !video.paused && video.currentTime > 0.15;
    })()`);
    const size = await evaluate("(() => { const r = document.querySelector('.agent-desk').getBoundingClientRect(); return [r.width, r.height]; })()");
    assert.deepEqual(size, [112, 112]);
    const screenshot = await command("Page.captureScreenshot", { format: "png" });
    await writeFile(join(output, `${state}.png`), Buffer.from(screenshot.data, "base64"));
    console.log(`${state}: autoplay 확인, ${join(output, `${state}.png`)}`);
  }

  await click("idle");
  await delay(160);
  const opacities = await evaluate("[...document.querySelectorAll('.agent-desk-layer')].map(el => Number(getComputedStyle(el).opacity))");
  assert.equal(opacities.length, 2, "크로스페이드에는 두 레이어가 필요하다");
  assert.ok(opacities.every((opacity) => opacity > 0 && opacity < 1), `전환 중 opacity: ${opacities}`);
  console.log(`crossfade: 두 레이어 opacity ${opacities.join(' / ')}`);

  await command("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-motion", value: "reduce" }] });
  for (const state of states) {
    await click(state);
    await waitFor(`(() => {
      const img = document.querySelector('.agent-desk-poster');
      return !document.querySelector('video') && img?.getAttribute('src') === '/avatar/${state}.png'
        && img.complete && img.naturalWidth > 0;
    })()`);
  }
  mediaRequests.length = 0;
  await command("Page.navigate", { url: `${url}?state=thinking` });
  await waitFor("document.querySelector('.agent-desk-poster')?.complete === true");
  assert.equal(await evaluate("document.querySelectorAll('video').length"), 0);
  assert.equal(mediaRequests.length, 0, "모션 줄이기로 시작하면 영상 요청도 없어야 한다");
  assert.deepEqual(errors, [], "브라우저 실행 오류");
  console.log("reduced-motion: 다섯 poster, 실행 중 설정 전환, 새 페이지의 영상 요청 0건 확인");
} finally {
  socket?.close();
  if (chrome && chrome.exitCode === null) {
    const exited = once(chrome, "exit");
    chrome.kill();
    const timeout = setTimeout(() => chrome.kill("SIGKILL"), 5000);
    await exited;
    clearTimeout(timeout);
  }
  await server?.close();
  await rm(profile, { recursive: true, force: true });
}
