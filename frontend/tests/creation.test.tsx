// @vitest-environment jsdom
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, test, vi } from 'vitest';
import { App } from '../src/main';

const options = {
  domains: [{ id: 1, name: 'Domain' }],
  groups: [{ id: 2, name: 'Group' }],
  traffic_sources: [{ id: 3, name: 'Source' }],
};
const created = { id: 42, name: 'Test campaign', state: 'disabled', geo_stream_id: 10, offer_stream_id: 11 };
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { 'Content-Type': 'application/json' },
});

afterEach(() => { cleanup(); sessionStorage.clear(); vi.unstubAllGlobals(); });

async function fillForm() {
  const user = userEvent.setup();
  await user.click(screen.getByRole('button', { name: 'Создать кампанию', exact: true }));
  await screen.findByRole('option', { name: 'Domain' });
  await user.type(screen.getByLabelText('Название'), 'Test campaign');
  await user.type(screen.getByLabelText('Код страны'), 'MX');
  await user.type(screen.getByLabelText('ID оффера'), '10');
  await user.selectOptions(screen.getByLabelText('Домен'), '1');
  await user.selectOptions(screen.getByLabelText('Группа'), '2');
  await user.selectOptions(screen.getByLabelText('Источник'), '3');
  return user;
}

test('creation finishes while another section is open and result survives navigation', async () => {
  let complete!: (value: Response) => void;
  const pending = new Promise<Response>((resolve) => { complete = resolve; });
  const fetchMock = vi.fn((url: string) => url === '/api/campaign-options'
    ? Promise.resolve(response(options)) : pending);
  vi.stubGlobal('fetch', fetchMock);
  render(<App />);
  const user = await fillForm();
  await user.click(screen.getByRole('button', { name: 'Создать выключенную кампанию' }));
  await user.click(screen.getByRole('button', { name: 'Редактор потоков' }));
  await act(async () => { complete(response(created)); await pending; });
  await user.click(screen.getByRole('button', { name: 'Создать кампанию', exact: true }));
  expect(await screen.findByText('Кампания 42 «Test campaign» создана и выключена.')).toBeTruthy();
  expect((screen.getByLabelText('Название') as HTMLInputElement).value).toBe('Test campaign');
  expect(fetchMock.mock.calls.filter(([url]) => url === '/api/campaigns')).toHaveLength(1);
  await user.click(screen.getByRole('button', { name: 'Открыть редактор' }));
  expect((screen.getByLabelText('ID кампании') as HTMLInputElement).value).toBe('42');
  await user.click(screen.getByRole('button', { name: 'Создать кампанию', exact: true }));
  expect(screen.getByText('Кампания 42 «Test campaign» создана и выключена.')).toBeTruthy();
});

test('retry after network failure reuses the operation ID, including after navigation', async () => {
  const bodies: { operation_id: string }[] = [];
  vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
    if (url === '/api/campaign-options') return Promise.resolve(response(options));
    bodies.push(JSON.parse(String(init?.body)));
    if (bodies.length === 1) return Promise.reject(new TypeError('Connection lost'));
    return Promise.resolve(response(created));
  }));
  render(<App />);
  const user = await fillForm();
  await user.click(screen.getByRole('button', { name: 'Создать выключенную кампанию' }));
  await screen.findByText('Connection lost');
  await user.click(screen.getByRole('button', { name: 'Редактор потоков' }));
  await user.click(screen.getByRole('button', { name: 'Создать кампанию', exact: true }));
  await user.click(screen.getByRole('button', { name: 'Создать выключенную кампанию' }));
  await waitFor(() => expect(bodies).toHaveLength(2));
  expect(bodies[0].operation_id).toMatch(/^[0-9a-f-]{36}$/);
  expect(bodies[1].operation_id).toBe(bodies[0].operation_id);
});


test('operation ID survives remount after a lost response', async () => {
  const ids: string[] = [];
  vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
    if (url === '/api/campaign-options') return Promise.resolve(response(options));
    ids.push(JSON.parse(String(init?.body)).operation_id);
    return Promise.reject(new TypeError('Connection lost'));
  }));
  const first = render(<App />);
  let user = await fillForm();
  await user.click(screen.getByRole('button', { name: 'Создать выключенную кампанию' }));
  await screen.findByText('Connection lost');
  first.unmount();
  render(<App />);
  user = await fillForm();
  await user.click(screen.getByRole('button', { name: 'Создать выключенную кампанию' }));
  await screen.findByText('Connection lost');
  expect(ids).toHaveLength(2);
  expect(ids[1]).toBe(ids[0]);
});


test('creation displays fields and reasons from a FastAPI validation response', async () => {
  vi.stubGlobal('fetch', vi.fn((url: string) => Promise.resolve(response(
    url === '/api/campaign-options' ? options : { detail: [
      { loc: ['body', 'offer_id'], msg: 'Input should be greater than 0' },
      { loc: ['body', 'country'], msg: 'String should match pattern' },
    ] }, url === '/api/campaign-options' ? 200 : 422,
  ))));
  render(<App />);
  const user = await fillForm();
  await user.click(screen.getByRole('button', { name: 'Создать выключенную кампанию' }));
  const alert = await screen.findByRole('alert');
  expect(alert.textContent).toBe('ID оффера: Input should be greater than 0; Код страны: String should match pattern');
  expect((screen.getByLabelText('Название') as HTMLInputElement).value).toBe('Test campaign');
  expect((screen.getByRole('button', { name: 'Создать выключенную кампанию' }) as HTMLButtonElement).disabled).toBe(false);
});

test('stream editor displays the field and reason when adding an offer fails validation', async () => {
  vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
    if (url === '/api/campaign-options') return Promise.resolve(response(options));
    if (url.endsWith('/streams')) return Promise.resolve(response([]));
    if (url.endsWith('/drafts')) return Promise.resolve(response([{
      id: 2, name: 'Offers', editable: true, dirty: false, conflict: false, revision: 0, offers: [],
    }]));
    if (url.endsWith('/draft') && init?.method === 'POST') return Promise.resolve(response({ detail: [
      { loc: ['body', 'offer_id'], msg: 'Value error, offer is unavailable' },
    ] }, 422));
    throw new Error(`Unexpected request: ${url}`);
  }));
  render(<App />);
  const user = userEvent.setup();
  await user.type(screen.getByLabelText('ID кампании'), '1');
  await user.click(screen.getByRole('button', { name: 'Fetch Streams' }));
  await screen.findByRole('heading', { name: 'Offers' });
  const editor = within(screen.getByRole('main'));
  const input = editor.getByLabelText('ID оффера') as HTMLInputElement;
  await user.type(input, '20');
  await user.click(editor.getByRole('button', { name: 'Add' }));
  expect((await screen.findByRole('alert')).textContent).toBe('ID оффера: Value error, offer is unavailable');
  expect(input.value).toBe('20');
});
