// Stand-in for https://telegram.org/js/telegram-web-app.js in e2e: records sendData, draws the MainButton
// (in Telegram it is native UI below the web view) so it can be clicked and screenshotted.
window.__sent = []; window.__alerts = [];
window.__lastMarker = () => { try { return marker && marker.getLatLng(); } catch (e) { return null; } };
window.__mb = {
  visible: false, params: {}, cb: null,
  setParams(p) { this.params = Object.assign(this.params, p); this.draw(); return this; },
  show() { this.visible = true; this.draw(); return this; },
  hide() { this.visible = false; this.draw(); return this; },
  onClick(f) { this.cb = f; return this; },
  draw() {
    if (!document.body) return;
    let b = document.getElementById('__mb');
    if (!b) {
      b = document.createElement('div'); b.id = '__mb';
      b.style.cssText = 'position:fixed;left:0;right:0;bottom:0;height:56px;z-index:5000;display:flex;align-items:center;' +
        'justify-content:center;font:700 15px Montserrat,system-ui,sans-serif;cursor:pointer';
      b.onclick = () => this.cb && this.cb();
      document.body.appendChild(b);
      const s = document.createElement('style');
      s.textContent = 'body.mb #map{bottom:56px!important} body.mb .radius{bottom:70px!important} body.mb .hint{bottom:122px!important}';
      document.head.appendChild(s);
    }
    b.textContent = this.params.text || '';
    b.style.background = this.params.color || '#2481cc';
    b.style.color = this.params.text_color || '#fff';
    b.style.display = this.visible ? 'flex' : 'none';
    document.body.classList.toggle('mb', this.visible);
  },
};
// window.__tgInitData (set by the test before the page loads) = initData signed with the test bot token
window.__opened = [];
window.Telegram = { WebApp: {
  ready() {}, expand() {}, setHeaderColor() {}, setBackgroundColor() {},
  showAlert(m) { window.__alerts.push(m); }, sendData(d) { window.__sent.push(d); },
  openLink(u) { window.__opened.push(u); }, openTelegramLink(u) { window.__opened.push(u); },
  BackButton: { visible: false, cb: null, show() { this.visible = true; }, hide() { this.visible = false; }, onClick(f) { this.cb = f; } },
  MainButton: window.__mb, initData: window.__tgInitData || '', initDataUnsafe: {}, colorScheme: 'light', platform: 'ios',
} };
