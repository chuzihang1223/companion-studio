'use strict'

/**
 * 读取 URL 已归一化的 IPv4 或 IPv4-mapped IPv6 地址。
 * WHATWG URL 会把 ::ffff:127.0.0.1 归一为 ::ffff:7f00:1；必须还原末尾四字节，
 * 对同一目标应用同样的 IPv4 策略，不能只按原始冒号文本判断。
 * @param {string} host 小写、去除方括号的 URL hostname。
 * @returns {number[]|null} 四个 IPv4 字节；普通域名/其他 IPv6 返回 null。
 */
function ipv4Octets(host) {
  const direct = host.split('.').map(Number)
  if (direct.length === 4 && direct.every(part => Number.isInteger(part) && part >= 0 && part <= 255)) {
    return direct
  }
  const mapped = /^::ffff:([0-9a-f]{1,4}):([0-9a-f]{1,4})$/.exec(host)
  if (!mapped) return null
  const high = Number.parseInt(mapped[1], 16)
  const low = Number.parseInt(mapped[2], 16)
  return [high >>> 8, high & 255, low >>> 8, low & 255]
}

/** 保持原有 IPv4 拒绝范围；映射表示不能绕过已有环回、私有和链路本地限制。 */
function privateIPv4(octets) {
  if (!octets) return false
  return octets[0] === 10 || octets[0] === 127 || octets[0] === 0 ||
    octets[0] === 169 && octets[1] === 254 ||
    octets[0] === 172 && octets[1] >= 16 && octets[1] <= 31 ||
    octets[0] === 192 && octets[1] === 168
}

/**
 * 校验工作区网页 URL：只允许无凭据的标准 HTTPS，拒绝明显本机/内网目标。
 * 这是 URL 字面量策略，不执行 DNS 解析，也不代替任务的网页访问授权。
 * @param {unknown} url 待打开、导航或重定向的地址。
 * @returns {string} WHATWG URL 归一后的合法 HTTPS 地址。
 * @throws {Error} 协议、凭据、端口或目标地址不允许。
 */
function safeHttps(url) {
  const parsed = new URL(String(url))
  if (parsed.protocol !== 'https:' || parsed.username || parsed.password || !parsed.hostname || parsed.port && parsed.port !== '443') {
    throw new Error('工作区只允许打开 HTTPS 网页')
  }
  const host = parsed.hostname.toLowerCase().replace(/^\[|\]$/g, '')
  if (host === 'localhost' || host.endsWith('.localhost') || host === '::1' ||
      host.includes(':') && (host.startsWith('fc') || host.startsWith('fd') || host.startsWith('fe80:')) ||
      privateIPv4(ipv4Octets(host))) {
    throw new Error('工作区不能打开本机或内网地址')
  }
  return parsed.toString()
}

module.exports = { safeHttps }
