/**
 * 图片处理：按需求 4.5 在前端把长边压到 ≤1920px（等比缩放），再上传。
 * 长边本来就 ≤1920 的图片直接复用原文件，不做无意义的二次编码。
 */

export interface CompressResult {
  blob: Blob
  dataUrl: string
  filename: string
  width: number
  height: number
  originalWidth: number
  originalHeight: number
  originalSize: number
  size: number
  compressed: boolean
}

export const MAX_EDGE = 1920
export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024
export const ACCEPTED_TYPES = ['image/jpeg', 'image/jpg', 'image/png', 'image/webp']

function readAsDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = () => reject(new Error('读取图片失败'))
    reader.readAsDataURL(blob)
  })
}

async function loadBitmap(file: Blob): Promise<ImageBitmap> {
  if (typeof createImageBitmap === 'function') {
    return await createImageBitmap(file)
  }
  // 兜底：老浏览器用 <img> 解码
  const url = URL.createObjectURL(file)
  try {
    const image = await new Promise<HTMLImageElement>((resolve, reject) => {
      const img = new Image()
      img.onload = () => resolve(img)
      img.onerror = () => reject(new Error('图片解码失败'))
      img.src = url
    })
    return await createImageBitmap(image)
  } finally {
    URL.revokeObjectURL(url)
  }
}

function canvasToBlob(canvas: HTMLCanvasElement, type: string, quality: number): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => (blob ? resolve(blob) : reject(new Error('图片压缩失败'))),
      type,
      quality,
    )
  })
}

function renameExt(filename: string, ext: string): string {
  const base = filename.replace(/\.[^.]+$/, '') || 'upload'
  return `${base}${ext}`
}

/**
 * 检测尺寸并按长边 ≤ maxEdge 等比缩放，导出 JPEG（质量 0.9）。
 */
export async function compressImage(file: File, maxEdge = MAX_EDGE): Promise<CompressResult> {
  if (!ACCEPTED_TYPES.includes(file.type)) {
    throw new Error('仅支持 JPG / PNG / WEBP 格式')
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    throw new Error('图片不能超过 10MB')
  }

  const bitmap = await loadBitmap(file)
  const originalWidth = bitmap.width
  const originalHeight = bitmap.height
  const longest = Math.max(originalWidth, originalHeight)

  if (longest <= maxEdge) {
    const dataUrl = await readAsDataUrl(file)
    bitmap.close?.()
    return {
      blob: file,
      dataUrl,
      filename: file.name,
      width: originalWidth,
      height: originalHeight,
      originalWidth,
      originalHeight,
      originalSize: file.size,
      size: file.size,
      compressed: false,
    }
  }

  const scale = maxEdge / longest
  const targetWidth = Math.round(originalWidth * scale)
  const targetHeight = Math.round(originalHeight * scale)

  const canvas = document.createElement('canvas')
  canvas.width = targetWidth
  canvas.height = targetHeight
  const context = canvas.getContext('2d')
  if (!context) {
    bitmap.close?.()
    throw new Error('当前浏览器不支持 Canvas 压缩')
  }
  context.imageSmoothingEnabled = true
  context.imageSmoothingQuality = 'high'
  context.drawImage(bitmap, 0, 0, targetWidth, targetHeight)
  bitmap.close?.()

  const blob = await canvasToBlob(canvas, 'image/jpeg', 0.9)
  const dataUrl = await readAsDataUrl(blob)
  return {
    blob,
    dataUrl,
    filename: renameExt(file.name, '.jpg'),
    width: targetWidth,
    height: targetHeight,
    originalWidth,
    originalHeight,
    originalSize: file.size,
    size: blob.size,
    compressed: true,
  }
}

/** 把草稿里的 DataURL 还原成可上传的 Blob */
export function dataUrlToBlob(dataUrl: string): Blob {
  const [head, body] = dataUrl.split(',')
  const mime = /data:([^;]+)/.exec(head)?.[1] || 'image/jpeg'
  const binary = atob(body || '')
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i)
  }
  return new Blob([bytes], { type: mime })
}
