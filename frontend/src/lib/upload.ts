// Photos go up resized in the browser: phone originals (4000px, 5–12 MB) were the slow part of an upload.
// 2560px long side, JPEG 0.85 — more than Telegram keeps (it shows ≤2560px), ~1 MB a photo.
export async function shrinkPhoto(f: File, max = 2560, quality = 0.85): Promise<File> {
  if (!f.type.startsWith('image/') || f.type === 'image/gif') return f;
  let bmp: ImageBitmap;
  try { bmp = await createImageBitmap(f, { imageOrientation: 'from-image' }); } catch { return f; } // e.g. HEIC outside Safari: the server copes
  const k = Math.min(1, max / Math.max(bmp.width, bmp.height));
  if (k === 1 && f.size < 1.5e6) { bmp.close(); return f; }
  const c = document.createElement('canvas');
  c.width = Math.round(bmp.width * k);
  c.height = Math.round(bmp.height * k);
  const ctx = c.getContext('2d')!;
  ctx.fillStyle = '#fff'; // transparent PNG → white, not black
  ctx.fillRect(0, 0, c.width, c.height);
  ctx.drawImage(bmp, 0, 0, c.width, c.height);
  bmp.close();
  const blob = await new Promise<Blob | null>((r) => c.toBlob(r, 'image/jpeg', quality));
  if (!blob || blob.size >= f.size) return f;
  return new File([blob], f.name.replace(/\.[^.]+$/, '') + '.jpg', { type: 'image/jpeg' });
}

/** Run `fn` over `items`, `n` at a time, in order of start. */
export async function pool<T>(items: T[], n: number, fn: (x: T, i: number) => Promise<void>): Promise<void> {
  let next = 0;
  await Promise.all(Array.from({ length: Math.min(n, items.length) }, async () => {
    while (next < items.length) { const i = next++; await fn(items[i], i); }
  }));
}
