export const IMAGE_FILE_ACCEPT = 'image/jpeg,image/png,image/webp,image/gif'

const ALLOWED_IMAGE_TYPES = new Set(IMAGE_FILE_ACCEPT.split(','))
const MAX_IMAGE_BYTES = 5 * 1024 * 1024

export function getImageFileError(file, label = '图片') {
  if (!file) return ''
  if (file.type && !ALLOWED_IMAGE_TYPES.has(file.type)) {
    return `${label}仅支持 JPG、PNG、WebP 或 GIF 格式。`
  }
  if (file.size > MAX_IMAGE_BYTES) {
    return `${label}图片不能超过 5MB。`
  }
  return ''
}
