const statusNode = document.querySelector('#status');
const forecastStatus = document.querySelector('#forecast-status');
const filters = document.querySelector('#filters');
const forecastForm = document.querySelector('#forecast');
let current = null;
let currentForecast = null;
let requestGeneration = 0;
let forecastGeneration = 0;

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '请求失败');
  return data;
}

function text(tag, value) {
  const node = document.createElement(tag);
  node.textContent = typeof value === 'object' ? JSON.stringify(value) : String(value ?? '未知');
  return node;
}

function amount(value) {
  return value == null ? '—' : Number(value).toFixed(1);
}

function interval(low, high) {
  return low == null ? '—' : `${amount(low)} – ${amount(high)}`;
}

function table(container, rows) {
  container.replaceChildren();
  if (!rows.length) {
    container.append(text('p', '当前范围没有记录'));
    return;
  }
  const columns = Array.from(new Set(rows.flatMap(row => Object.keys(row))));
  const grid = document.createElement('table');
  const head = document.createElement('tr');
  for (const column of columns) head.append(text('th', column));
  const thead = document.createElement('thead');
  thead.append(head);
  grid.append(thead);
  const body = document.createElement('tbody');
  for (const row of rows) {
    const line = document.createElement('tr');
    for (const column of columns) line.append(text('td', row[column]));
    body.append(line);
  }
  grid.append(body);
  container.append(grid);
}

function renderHistory(value, savedTitle = '') {
  current = value;
  const summary = document.querySelector('#summary');
  summary.replaceChildren();
  for (const [key, count] of Object.entries(value.result.summary)) {
    summary.append(text('dt', key), text('dd', count));
  }
  document.querySelector('#notes').replaceChildren(...value.result.notes.map(note => text('li', note)));
  table(document.querySelector('#results'), value.result.rows);
  statusNode.textContent = savedTitle ? `正在查看保存的分析：${savedTitle}` : '分析已更新';
}

function render(value, savedTitle = '') {
  filters.elements.cutoff.value = value.options.cutoff;
  filters.elements.group.value = value.options.group;
  document.querySelector('#digest').textContent = `资料指纹：${value.digest}`;
  if (value.options.mode === 'forecast') {
    currentForecast = value;
    statusNode.textContent = savedTitle ? `正在查看保存的预测：${savedTitle}` : statusNode.textContent;
    renderForecast(value, savedTitle);
    return;
  }
  renderHistory(value, savedTitle);
  if (!currentForecast) document.querySelector('#forecast-status').textContent = '预测尚未更新';
}

function rowsOfLevels(levels) {
  return [...levels.categories.map(level => ({...level, kind: '品类'})), ...levels.regions.map(level => ({...level, kind: '区域'})), {...levels.total, kind: '全部'}].map(level => ({
    '层级': level.level,
    '范围': level.label,
    '参与序列': level.series,
    '首周点预测': amount(level.forecast[0]),
    '首周80%区间': interval(level.low[0], level.high[0]),
    '末周点预测': amount(level.forecast.slice(-1)[0]),
    '序列间相关': level.correlation,
    '计划回测误差': level.plan_rmse == null ? '—' : level.plan_rmse,
    '未纳入': level.excluded.join('；') || '—',
  }));
}

function renderForecast(value, savedTitle) {
  currentForecast = value;
  const result = value.result;
  document.querySelector('#forecast-status').textContent = savedTitle ? `正在查看保存的预测：${savedTitle}` : `预测已更新：时点 ${value.options.cutoff} 之后 ${result.horizon} 周`;
  const summary = document.querySelector('#forecast-summary');
  summary.replaceChildren();
  for (const [key, count] of Object.entries(result.summary)) {
    summary.append(text('dt', key), text('dd', count));
  }
  summary.append(text('dt', '回测起点'), text('dd', result.validation.origins.length ? `${result.validation.origins[0]}–${result.validation.origins.slice(-1)[0]}` : '无'));
  document.querySelector('#forecast-notes').replaceChildren(...result.notes.map(note => text('li', note)));
  table(document.querySelector('#levels'), rowsOfLevels(result.levels));
  table(document.querySelector('#series'), result.rows.map(row => ({
    '商品': `${row.name}（${row.product}）`,
    '区域': row.region,
    '品类': row.category ?? '未归类',
    '状态': row.status_label,
    '模型': row.models.join('＋') || '—',
    '#1 周': interval(row.low?.[0], row.high?.[0]),
    '#2 周': interval(row.low?.[1], row.high?.[1]),
    '#3 周': interval(row.low?.[2], row.high?.[2]),
    '#4 周': interval(row.low?.[3], row.high?.[3]),
    '区间依据': row.interval_basis.join('；') || '—',
    '有效观测': row.observations,
    '缺记录周': row.missing_weeks.length,
    '零销量周': row.zero_weeks.length,
    '序列误差': row.validation.series_rmse ?? '—',
    '计划误差': row.validation.plan_rmse ?? '—',
  })));
  const scenario = result.scenario;
  document.querySelector('#scenario').replaceChildren();
  document.querySelector('#scenario-notes').replaceChildren();
  if (!scenario) {
    document.querySelector('#scenario').append(text('p', '尚未设置替代情景。选择旧品与新品后更新预测，即可比较替换前后的层级计划。'));
    return;
  }
  const heading = text('p', `${scenario.from} → ${scenario.to}，替代比例 ${Math.round(scenario.share * 100)}%，来源：${scenario.source_basis ?? '不可量化'}；旧品${scenario.source_status}，新品${scenario.target_status}`);
  if (scenario.levels.length) {
    table(document.querySelector('#scenario'), scenario.levels.map(level => ({
      '层级': level.level,
      '范围': level.label,
      '基线首周': amount(level.baseline[0]),
      '情景首周': amount(level.scenario[0]),
      '首周变化': amount(level.delta[0]),
      '可支持到': level.support,
    })));
    document.querySelector('#scenario').prepend(heading);
  } else {
    document.querySelector('#scenario').append(heading, text('p', '该情景没有可量化的层级变化'));
  }
  const products = scenario.products || [];
  if (products.length) {
    const detail = document.createElement('div');
    detail.className = 'scroll';
    table(detail, products.flatMap(item => [
      item.source && {
        '区域': item.region,
        '角色': `旧品 ${scenario.from}`,
        '基线首周': amount(item.source.forecast[0]),
        '情景首周': amount(item.source.scenario[0]),
        '首周变化': amount(item.source.scenario[0] - item.source.forecast[0]),
      },
      item.target && {
        '区域': item.region,
        '角色': `新品 ${scenario.to}`,
        '基线首周': amount(item.target.forecast[0]),
        '情景首周': amount(item.target.scenario[0]),
        '首周变化': amount(item.target.scenario[0] - item.target.forecast[0]),
      },
    ].filter(Boolean)));
    const note = text('p', '单品层位移不会进入品类与总层（同品类同区域替换时两层变化为零），这正是替代情景与普通预测需要并用查看的原因。');
    document.querySelector('#scenario').append(detail, note);
  }
  document.querySelector('#scenario-notes').replaceChildren(...scenario.notes.map(note => text('li', note)));
  forecastForm.elements.horizon.value = result.horizon;
  forecastForm.elements.share.value = scenario?.share ?? 1;
  forecastForm.elements.from.value = scenario?.from ?? '';
  forecastForm.elements.to.value = scenario?.to ?? '';
}

async function refresh() {
  const generation = ++requestGeneration;
  const query = new URLSearchParams(new FormData(filters));
  statusNode.textContent = '正在分析';
  const generationForecast = ++forecastGeneration;
  forecastStatus.textContent = '正在计算预测';
  try {
    const [result, forecast] = await Promise.all([
      api(`/api/view?${query}`),
      api(`/api/forecast?${forecastQuery()}`),
    ]);
    if (generation === requestGeneration) renderHistory(result);
    if (generationForecast === forecastGeneration) {
      currentForecast = forecast;
      renderForecast(forecast, '');
    }
  } catch (error) {
    if (generation === requestGeneration) {
      current = null;
      statusNode.textContent = error.message;
    }
    if (generationForecast === forecastGeneration) forecastStatus.textContent = error.message;
  }
}

function forecastQuery() {
  const query = new URLSearchParams(new FormData(filters));
  query.set('mode', 'forecast');
  query.set('horizon', forecastForm.elements.horizon.value || '4');
  const from = forecastForm.elements.from.value;
  const to = forecastForm.elements.to.value;
  if (from && to) {
    query.set('from', from);
    query.set('to', to);
    query.set('share', forecastForm.elements.share.value || '1');
  }
  return query;
}

async function refreshForecast() {
  const generation = ++forecastGeneration;
  const query = forecastQuery();
  forecastStatus.textContent = '正在计算预测';
  try {
    const value = await api(`/api/forecast?${query}`);
    if (generation === forecastGeneration) {
      currentForecast = value;
      renderForecast(value, '');
    }
  } catch (error) {
    if (generation === forecastGeneration) {
      currentForecast = null;
      forecastStatus.textContent = error.message;
    }
  }
}

async function history() {
  const entries = await api('/api/analyses');
  const list = document.querySelector('#history');
  list.replaceChildren();
  for (const entry of entries) {
    const item = document.createElement('li');
    const button = text('button', entry.title);
    button.type = 'button';
    button.addEventListener('click', async () => {
      const generation = ++requestGeneration;
      try {
        const saved = await api(`/api/analyses/${entry.id}`);
        if (generation === requestGeneration) render(saved, saved.title);
      } catch (error) {
        statusNode.textContent = error.message;
      }
    });
    item.append(button, text('small', entry.created_at));
    list.append(item);
  }
}

filters.addEventListener('submit', event => {
  event.preventDefault();
  refresh();
});

forecastForm.addEventListener('submit', event => {
  event.preventDefault();
  refreshForecast();
});

document.querySelector('#save').addEventListener('submit', async event => {
  event.preventDefault();
  if (!current || current.options.mode === 'forecast') {
    statusNode.textContent = '请先更新历史分析再保存';
    return;
  }
  try {
    const saved = await api('/api/analyses', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({title: event.target.elements.title.value, options: current.options})});
    render(saved, saved.title);
    await history();
  } catch (error) {
    statusNode.textContent = error.message;
  }
});

document.querySelector('#save-forecast').addEventListener('submit', async event => {
  event.preventDefault();
  if (!currentForecast) {
    forecastStatus.textContent = '请先更新预测再保存';
    return;
  }
  try {
    const saved = await api('/api/analyses', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({title: event.target.elements.title.value, options: currentForecast.options})});
    render(saved, saved.title);
    await history();
  } catch (error) {
    forecastStatus.textContent = error.message;
  }
});

document.querySelector('#source').addEventListener('click', async () => {
  try {
    const data = await api(`/api/source?table=${encodeURIComponent(document.querySelector('#tables').value)}`);
    table(document.querySelector('#raw'), data.rows);
  } catch (error) {
    statusNode.textContent = error.message;
  }
});

function productOption(select, value, label) {
  const option = text('option', label);
  option.value = value;
  select.append(option);
}

async function start() {
  const catalog = await api('/api/catalog');
  document.querySelector('#description').textContent = catalog.description;
  filters.elements.cutoff.min = catalog.min_cutoff;
  filters.elements.cutoff.max = catalog.max_cutoff;
  filters.elements.cutoff.value = catalog.default_cutoff;
  const groups = [['', '全部'], ...catalog.groups.map(group => [group, group])];
  for (const [key, label] of groups) {
    const option = text('option', label);
    option.value = key;
    filters.elements.group.append(option);
  }
  for (const select of [forecastForm.elements.from, forecastForm.elements.to]) {
    productOption(select, '', '不设置情景');
  }
  for (const row of catalog.products) {
    productOption(forecastForm.elements.from, row.product, `${row.name}（${row.product}）`);
    productOption(forecastForm.elements.to, row.product, `${row.name}（${row.product}）`);
  }
  forecastForm.elements.from.value = '';
  forecastForm.elements.to.value = '';
  for (const name of Object.keys(catalog.tables)) {
    const option = text('option', `${name}（${catalog.tables[name]}）`);
    option.value = name;
    document.querySelector('#tables').append(option);
  }
  await refresh();
  await refreshForecast();
  await history();
}

start().catch(error => { statusNode.textContent = error.message; });
