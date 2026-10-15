'use strict'

const assert = require('node:assert/strict')
const test = require('node:test')
const { safeHttps } = require('../../desktop/electron/url-policy.cjs')

test('IPv4-mapped IPv6 cannot bypass the existing private IPv4 policy', () => {
  const addresses = [
    'https://[::ffff:127.0.0.1]/',
    'https://[::ffff:10.1.2.3]/',
    'https://[::ffff:172.16.0.1]/',
    'https://[::ffff:172.31.255.255]/',
    'https://[::ffff:192.168.1.1]/',
    'https://[::ffff:169.254.1.1]/',
    'https://[::ffff:0.0.0.1]/',
    'https://[::ffff:7f00:1]/',
    'https://[0:0:0:0:0:ffff:7f00:0001]/',
  ]
  for (const url of addresses) assert.throws(() => safeHttps(url), /本机或内网/, url)
})

test('existing direct local addresses remain denied', () => {
  for (const host of ['localhost', 'tools.localhost', '127.0.0.1', '10.1.2.3', '172.16.0.1', '172.31.255.255', '192.168.1.1', '169.254.1.1', '0.0.0.1', '[::1]', '[fc00::1]', '[fd12::1]', '[fe80::1]']) {
    assert.throws(() => safeHttps(`https://${host}/`), /本机或内网/, host)
  }
})

test('public URLs and public mapped addresses keep their normalized result', () => {
  for (const url of ['https://example.org/docs?query=a%20b#section', 'https://example.org:443/', 'https://8.8.8.8/', 'https://172.15.255.255/', 'https://172.32.0.1/', 'https://[2001:4860:4860::8888]/', 'https://[::ffff:8.8.8.8]/', 'https://[::ffff:1.1.1.1]/', 'https://[::ffff:172.15.255.255]/', 'https://[::ffff:172.32.0.1]/']) {
    assert.equal(safeHttps(url), new URL(url).toString(), url)
  }
})

test('protocol, credentials and nonstandard port restrictions are unchanged', () => {
  for (const url of ['http://example.org/', 'file:///example', 'javascript:alert(1)', 'https://user@example.org/', 'https://user:password@example.org/', 'https://example.org:8443/']) {
    assert.throws(() => safeHttps(url), /只允许打开 HTTPS/, url)
  }
  assert.throws(() => safeHttps('not a url'), TypeError)
})
