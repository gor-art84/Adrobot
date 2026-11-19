import { afterEach, expect, test, vi } from 'vitest';
import { request } from '../src/api/request';

const fallback = 'Не удалось выполнить запрос.';
function reply(body: unknown, status = 422) {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status })));
}
afterEach(() => vi.unstubAllGlobals());

test('422 reports all validation fields and their original reasons', async () => {
  reply({ detail: [
    { loc: ['body', 'offer_id'], msg: 'Input should be greater than 0', type: 'greater_than', input: -1 },
    { loc: ['body', 'country'], msg: 'String should match pattern', type: 'string_pattern_mismatch' },
  ] });
  await expect(request('/api/campaigns')).rejects.toThrow(
    'ID оффера: Input should be greater than 0; Код страны: String should match pattern',
  );
});

test.each([
  [['path', 'campaign_id'], 'ID кампании'],
  [['query', 'limit'], 'limit'],
  [['header', 'x-token'], 'x-token'],
  [['cookie', 'session'], 'session'],
  [['body', 'offers', 0, 'offer_id'], 'offers[0].ID оффера'],
  [['body', 'body', 'custom_field'], 'body.custom_field'],
  [['revision'], 'Ревизия черновика'],
  [['body', 'constructor'], 'constructor'],
  [['body'], 'Запрос'],
  [[], 'Запрос'],
  [undefined, 'Запрос'],
  ['offer_id', 'Запрос'],
  [['body', null], 'Запрос'],
])('formats validation location %j', async (loc, field) => {
  reply({ detail: [{ loc, msg: 'Field required' }] });
  await expect(request('/api/test')).rejects.toThrow(`${field}: Field required`);
});

test.each([
  { detail: [] },
  { detail: [null, 'invalid', {}, { msg: 123 }, { msg: ' ' }] },
  { detail: {} },
  { detail: { message: 123 } },
  { detail: '' },
  { detail: null },
  {},
  null,
  'error',
])('malformed error body %j uses a useful fallback', async (body) => {
  reply(body);
  await expect(request('/api/test')).rejects.toThrow(fallback);
});

test('ignores malformed entries without losing valid validation errors', async () => {
  reply({ detail: [null, {}, { msg: 12 }, { loc: ['body', 'name'], msg: 'Field required' }] });
  await expect(request('/api/test')).rejects.toThrow('Название: Field required');
});

test('preserves string errors', async () => {
  reply({ detail: 'Оффер 20 неактивен в Keitaro.' });
  await expect(request('/api/test')).rejects.toThrow('Оффер 20 неактивен в Keitaro.');
});

test('preserves structured errors with campaign details', async () => {
  reply({ detail: { message: 'Кампания 42 создана. Проверьте alias adrobot-test.', campaign_id: 42 } }, 503);
  await expect(request('/api/test')).rejects.toThrow('Кампания 42 создана. Проверьте alias adrobot-test.');
});

test('non-JSON errors use fallback', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('<html>Error</html>', { status: 502 })));
  await expect(request('/api/test')).rejects.toThrow(fallback);
});

test('success returns parsed data and forwards request options', async () => {
  reply({ id: 42 }, 201);
  const options = { method: 'POST', body: '{}' };
  await expect(request('/api/campaigns', options)).resolves.toEqual({ id: 42 });
  expect(fetch).toHaveBeenCalledWith('/api/campaigns', options);
});

test('network failures retain their reason', async () => {
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Connection lost')));
  await expect(request('/api/test')).rejects.toThrow('Connection lost');
});
