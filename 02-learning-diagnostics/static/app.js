'use strict';
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const state = { report: null, reports: [], schema: null, view: 'overview', loading: false, authRequired: false };
let token = sessionStorage.getItem('eduforge_diagnostics_token') || '';
const levels = { foundation: '基础巩固', consolidation: '强化提升', challenge: '拓展挑战' };
const statuses = { assessed: '可评估', insufficient_data: '数据不足', not_taught: '尚未授课', unknown_topic: '进度表之外' };
const priorities = { high: '优先补讲', medium: '建议复习', none: '持续观察', not_evaluated: '暂不评估' };
const esc = value => String(value ?? '').replace(/[&<>"']/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[character]));
const score = value => value === null || value === undefined || !Number.isFinite(Number(value)) ? '—' : Number(value).toFixed(1);
const count = value => Number.isFinite(Number(value)) ? Number(value).toLocaleString('zh-CN') : '—';
const when = value => { const date = new Date(value); return Number.isNaN(date.getTime()) ? String(value || '—') : date.toLocaleString('zh-CN', { hour12: false, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }); };
let toastTimer;
function toast(message) { $('#toast').textContent = message; $('#toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 4500); }
function detailMessage(detail) { if (typeof detail === 'string') return detail; if (Array.isArray(detail)) return detail.map(item => item.msg || JSON.stringify(item)).join('；'); return detail?.message || JSON.stringify(detail || '请求失败'); }
async function api(path, options = {}) {
  const response = await fetch('/api/diagnostics' + path, { ...options, headers: { ...(token ? { Authorization: 'Bearer ' + token } : {}), ...(options.headers || {}) } });
  if (!response.ok) { if (response.status === 401) authPrompt(); let body; try { body = await response.json(); } catch { body = null; } throw new Error(body ? detailMessage(body.detail ?? body) : `请求失败（${response.status}）`); }
  return response.json();
}
function setConnection(ok) { $('#connection-dot').className = 'dot ' + (ok ? 'connected' : 'failed'); $('#connection-text').textContent = ok ? '服务已连接' : '连接失败'; }
function navigate() {
  const desired = location.hash.replace(/^#/, '').split('?')[0];
  state.view = ['overview', 'students', 'import', 'history'].includes(desired) ? desired : 'overview';
  $$('.view').forEach(view => {
    view.hidden = view.id !== 'view-' + state.view;
    const heading = $('.page-head h1, .page-head h2', view);
    const desiredTag = view.hidden ? 'H2' : 'H1';
    if (heading && heading.tagName !== desiredTag) { const replacement = document.createElement(desiredTag.toLowerCase()); [...heading.attributes].forEach(attribute => replacement.setAttribute(attribute.name, attribute.value)); replacement.append(...heading.childNodes); heading.replaceWith(replacement); }
  });
  $$('[data-nav]').forEach(link => { if (link.dataset.nav === state.view) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current'); });
  document.title = ({ overview: '班级报告', students: '学生与练习', import: '导入数据', history: '历史报告' }[state.view]) + ' · EduForge 学情诊断';
  if (state.view === 'history') renderHistory();
}
function evidenceList(values, empty = '尚无可展示的失分证据。') { return values?.length ? `<ul class="evidence-list">${values.map(item => `<li>${esc(item)}</li>`).join('')}</ul>` : `<p class="quiet">${esc(empty)}</p>`; }
function renderMethodology(methodology) {
  if (!methodology) return '<p>未提供方法说明。</p>';
  if (typeof methodology === 'string') return `<p>${esc(methodology)}</p>`;
  return `<p>${esc(methodology.description || '统计工具按显式评分规则计算，Agent 方案另列。')}</p><p>${esc(methodology.formula || '')}</p><p>${esc(methodology.missing_data || '')}</p><p>${esc(methodology.scope || '')}</p><details class="snapshot"><summary>完整评分标准与课程进度快照</summary><pre>${esc(JSON.stringify({ rubric: methodology.rubric, syllabus: methodology.syllabus }, null, 2))}</pre></details>`;
}
function renderReport(report) {
  state.report = report;
  $('#overview-empty').hidden = true;
  $('#report-content').hidden = false;
  $('#export-report').hidden = false;
  $('#overview-title').textContent = report.class_name;
  $('#scope').textContent = 'EduForge / ' + (report.course || '学情诊断');
  const week = report.methodology?.syllabus?.taught_through_week;
  $('#report-subtitle').textContent = `${report.course || '课程诊断'}${week !== undefined ? ` · 已授课至第 ${week} 周` : ''} · ${report.is_demo ? '合成示例报告' : '考核数据快照'}`;
  $('#report-source').textContent = '来源：' + (report.source_name || 'CSV 导入');
  $('#report-created').textContent = '生成于 ' + when(report.created_at);
  $('#demo-tag').hidden = !report.is_demo;
  renderReview(report);
  renderAgent(report);
  const summary = report.summary;
  const metrics = [
    ['参与学生', count(summary.student_count), '人', `${count(summary.assessed_student_count ?? report.students.filter(s => s.mastery !== null).length)} 人达到数据要求`],
    ['平均掌握度', score(summary.average_mastery), '分', '仅对可评估学生取平均'],
    ['需要基础巩固', count(summary.at_risk_count), '人', '依据本次有效成绩'],
    ['有效考核记录', count(summary.record_count), '条', `${count(summary.insufficient_student_count || 0)} 人暂不分层`]
  ];
  $('#kpis').innerHTML = metrics.map(([label, value, unit, note]) => `<dl class="kpi"><dt>${label}</dt><dd${value === '—' ? ' class="missing"' : ''}>${value}${value === '—' ? '' : `<small>${unit}</small>`}</dd><p>${note}</p></dl>`).join('');
  const high = report.reteaching.filter(item => item.priority === 'high').length;
  $('#report-finding').textContent = report.reteaching.length ? `本次有 ${report.reteaching.length} 个知识点建议补讲或复习${high ? `，其中 ${high} 个优先处理` : ''}。从下方具体失分证据开始核对。` : '现有有效记录没有触发班级补讲条件；尚未评估的知识点仍需要补充数据。';
  $('#topic-count').textContent = report.topics.length + ' 个知识点';
  $('#topic-rows').innerHTML = report.topics.map((topic, index) => `<tr><td><button class="row-link" data-topic="${index}">${esc(topic.topic)}</button><span class="subline">${topic.week ? `第 ${topic.week} 周 · ` : ''}${statuses[topic.status] || esc(topic.status)}</span></td><td class="num mastery-cell"><strong class="${topic.mastery === null ? 'missing' : ''}">${score(topic.mastery)}</strong>${topic.mastery === null ? '' : `<div class="mastery-track" aria-hidden="true"><span style="width:${Math.max(0, Math.min(100, Number(topic.mastery)))}%"></span></div>`}</td><td class="num">${topic.status === 'assessed' ? `${count(topic.weak_count)} / ${count(topic.assessed_count)}` : '<span class="missing">—</span>'}</td><td>${topic.priority === 'high' || topic.priority === 'medium' ? `<span class="tag ${topic.priority}">${priorities[topic.priority]}</span>` : `<span class="quiet">${priorities[topic.priority] || '暂不评估'}</span>`}</td></tr>`).join('');
  $('#reteach-count').textContent = report.reteaching.length + ' 项';
  $('#reteaching').innerHTML = report.reteaching.length ? report.reteaching.map(item => `<article class="reteach-item"><div class="reteach-title"><h3>${esc(item.topic)}</h3><span class="tag ${item.priority === 'high' ? 'high' : 'medium'}">${priorities[item.priority] || esc(item.priority)}</span></div><p>${esc(item.reason)}</p><p class="suggestion">${esc(item.suggestion)}</p></article>`).join('') : '<p class="empty-row">没有触发班级补讲条件。可查看个人练习安排，或补充缺失考核。</p>';
  $('#warnings').innerHTML = report.warnings.length ? report.warnings.map(message => `<li>${esc(message)}</li>`).join('') : '<li>没有发现需提示的数据质量问题。</li>';
  const tally = { foundation: 0, consolidation: 0, challenge: 0, insufficient_data: 0 };
  report.students.forEach(student => { tally[student.level || 'insufficient_data'] = (tally[student.level || 'insufficient_data'] || 0) + 1; });
  $('#level-counts').innerHTML = Object.entries(tally).map(([level, total]) => `<div class="level-line"><span>${levels[level] || '数据不足'}</span><strong>${count(total)} <span class="quiet">人</span></strong></div>`).join('');
  $('#report-methodology').innerHTML = renderMethodology(report.methodology);
  renderStudents();
}
function renderStudents() {
  const all = state.report?.students || [];
  const query = $('#student-search').value.trim().toLowerCase();
  const level = $('#student-level').value;
  const selected = all.filter(student => (!query || `${student.name} ${student.student_id}`.toLowerCase().includes(query)) && (!level || (level === 'insufficient_data' ? student.status === 'insufficient_data' : student.level === level)));
  $('#student-count').textContent = state.report ? `显示 ${selected.length} / ${all.length} 人` : '尚未选择报告';
  $('#student-rows').innerHTML = selected.map(student => `<tr><td><button class="row-link" data-student="${esc(student.student_id)}">${esc(student.name)}</button><span class="subline">${esc(student.student_id)}</span></td><td class="num">${score(student.mastery)}</td><td>${student.level ? `<span class="tag">${levels[student.level] || esc(student.level)}</span>` : '<span class="quiet">数据不足</span>'}</td><td>${student.weak_topics.length ? esc(student.weak_topics.join('、')) : '<span class="quiet">' + (student.status === 'assessed' ? '未发现已评估薄弱点' : '暂不判断') + '</span>'}</td><td class="num">${count(student.record_count ?? student.topic_scores.reduce((sum, topic) => sum + topic.record_count, 0))}</td><td><button class="row-action" data-student="${esc(student.student_id)}">详情与练习</button></td></tr>`).join('');
  $('#students-empty').hidden = !!selected.length;
  $('#students-empty').innerHTML = state.report ? '没有符合筛选条件的学生。' : '先生成或打开一份报告，再查看学生证据与练习。 <a class="text-link" href="#import">导入数据 →</a>';
}
function showStudent(id) {
  const student = state.report?.students.find(item => item.student_id === id);
  if (!student) return;
  $('#student-dialog-title').textContent = student.name;
  $('#student-dialog-id').textContent = student.student_id;
  $('#student-detail').innerHTML = `<div class="detail-stats"><div><strong>${score(student.mastery)}</strong><span>掌握度 / 100</span></div><span class="tag">${levels[student.level] || '数据不足，暂不分层'}</span></div><section class="detail-section"><h3>个人知识点</h3><table class="data-table"><thead><tr><th>知识点</th><th class="num">掌握度</th><th class="num">记录</th><th>判定</th></tr></thead><tbody>${student.topic_scores.map(topic => `<tr><td>${esc(topic.topic)}</td><td class="num">${score(topic.mastery)}</td><td class="num">${count(topic.record_count)}</td><td class="quiet">${statuses[topic.status] || esc(topic.status)}</td></tr>`).join('')}</tbody></table></section><section class="detail-section"><h3>失分证据与数据说明</h3>${evidenceList(student.evidence)}</section><section class="detail-section"><h3>本地题库参考 · ${student.exercises.length} 题</h3>${student.exercises.length ? student.exercises.map((exercise, index) => `<article class="exercise"><div class="exercise-meta"><strong>${index + 1}. ${esc(exercise.topic)}</strong><span class="tag">${levels[exercise.level] || esc(exercise.level)}</span></div><p>${esc(exercise.question)}</p><details><summary>查看提示</summary><p>${esc(exercise.hint)}</p></details><details><summary>查看参考答案</summary><p>${esc(exercise.answer)}</p></details></article>`).join('') : '<p class="quiet">暂未分配练习。请先补充有效考核记录或对应题库。</p>'}</section><p class="detail-note">这些建议只针对本次数据，不代表固定能力标签。请先独立作答，再展开提示和答案。</p>`;
  $('#student-dialog').showModal();
  const generated = (state.report?.agent?.result?.exercises || []).filter(exercise =>
    exercise.student_ids.length ? exercise.student_ids.includes(student.student_id) : exercise.level === student.level && student.topic_scores.some(topic => topic.topic === exercise.topic && topic.status === 'assessed'));
  $('#student-detail').insertAdjacentHTML('beforeend', `<section class="detail-section"><h3>Agent 生成的新练习</h3>${generated.length ? generated.map(exercise => renderAgentExercise(exercise)).join('') : '<p class="quiet">本次 Agent 尚未针对该学生或其层级生成练习。上方保留本地题库参考。</p>'}</section>`);
}
function showTopic(index) {
  const topic = state.report?.topics[index];
  if (!topic) return;
  $('#topic-dialog-title').textContent = topic.topic;
  $('#topic-detail').innerHTML = `<div class="detail-stats"><div><strong>${score(topic.mastery)}</strong><span>掌握度 / 100</span></div><span class="tag">${statuses[topic.status] || esc(topic.status)}</span></div><p class="quiet">${topic.week ? `计划第 ${topic.week} 周授课；` : ''}${count(topic.student_count)} 人有记录，${count(topic.assessed_count)} 人达到评估要求。</p><section class="detail-section"><h3>原始失分记录</h3>${evidenceList(topic.evidence, '当前没有可展示的失分记录；未评估不代表已经掌握。')}</section>${topic.error_tags && Object.keys(topic.error_tags).length ? `<section class="detail-section"><h3>明确标注的错因</h3>${Object.entries(topic.error_tags).map(([tag, total]) => `<div class="config-row"><span>${esc(tag)}</span><span>${count(total)} 条</span></div>`).join('')}</section>` : '<p class="detail-note">没有错因标签时，系统不会仅凭低分推断认知成因。</p>'}`;
  $('#topic-dialog').showModal();
}
function renderHistory() {
  const query = $('#history-search').value.trim().toLowerCase();
  const reports = state.reports.filter(report => `${report.class_name} ${report.course || ''}`.toLowerCase().includes(query));
  $('#history-count').textContent = `显示 ${reports.length} / ${state.reports.length} 份报告`;
  $('#history-rows').innerHTML = reports.map(report => `<tr><td><button class="row-link" data-report="${esc(report.id)}">${esc(report.class_name)}</button><span class="subline">${esc(report.course || '未提供课程')} · ${report.review_status === 'reviewed' ? '已人工复核' : '待人工复核'}</span></td><td class="num">${count(report.student_count)}</td><td class="num">${score(report.average_mastery)}</td><td>${esc(report.source_name || 'CSV 导入')}${report.is_demo ? '<span class="subline">合成示例</span>' : ''}</td><td class="num">${esc(when(report.created_at))}</td><td><button class="row-action" data-report="${esc(report.id)}">打开报告</button></td></tr>`).join('');
  $('#history-empty').hidden = !!reports.length;
  $('#history-empty').innerHTML = state.reports.length ? '没有符合搜索条件的报告。' : '还没有保存的报告。 <a class="text-link" href="#import">导入一份 CSV →</a>';
}
function renderSchema(schema) {
  state.schema = schema;
  $('#schema-rows').innerHTML = schema.columns.map(column => `<tr><td>${esc(column.label)}</td><td>${esc(column.key)}</td><td>${column.required ? '必需列' : '可选列'}</td><td>${esc(column.description)}</td></tr>`).join('');
  const rubric = schema.rubric, syllabus = schema.syllabus;
  const weights = rubric.weights;
  $('#default-config').innerHTML = [
    ['课程', syllabus.course], ['班级', syllabus.class_name], ['教学进度', `已授课至第 ${syllabus.taught_through_week} 周`],
    ['考核权重', `作业 ${Math.round(weights.homework * 100)}% / 随堂测 ${Math.round(weights.quiz * 100)}% / 互动 ${Math.round(weights.interaction * 100)}%`],
    ['数据要求', `每人每知识点至少 ${rubric.min_records_per_topic} 条`],
    ['练习分层', `< ${rubric.thresholds.weak} / ${rubric.thresholds.weak}—${rubric.thresholds.challenge} / ≥ ${rubric.thresholds.challenge} 分`]
  ].map(([label, value]) => `<div class="config-row"><span>${esc(label)}</span><span>${esc(value)}</span></div>`).join('');
  $('#default-config').innerHTML += `<details class="snapshot"><summary class="quiet">查看默认知识点与周次</summary>${syllabus.topics.map(topic => `<div class="config-row"><span>${esc(topic.topic)}</span><span>第 ${topic.week} 周</span></div>`).join('')}</details>`;
}
async function refreshHistory() { const data = await api('/reports'); state.reports = data.reports; renderHistory(); }
async function openReport(id) { const report = await api('/reports/' + encodeURIComponent(id)); renderReport(report); location.hash = 'overview'; window.scrollTo({ top: 0, behavior: 'instant' }); }
function setBusy(busy, label) { state.loading = busy; $$('[data-demo]').forEach(button => { button.disabled = busy; }); $('#upload-submit').disabled = busy; $('#upload-state').textContent = busy ? label : ''; }
async function createDemo() {
  if (state.loading) return;
  setBusy(true, '正在生成合成示例…');
  try { const report = await api('/demo', { method: 'POST' }); renderReport(report); await refreshHistory(); location.hash = 'overview'; window.scrollTo({ top: 0, behavior: 'instant' }); toast('已生成合成示例报告，所有数值来自示例 CSV。'); }
  catch (error) { toast('示例生成失败：' + error.message); }
  finally { setBusy(false, ''); }
}
async function upload(event) {
  event.preventDefault(); if (state.loading) return;
  const form = event.currentTarget, errorBox = $('#upload-error');
  errorBox.hidden = true;
  try {
    const csv = form.elements.file.files[0];
    if (!csv || !/\.csv$/i.test(csv.name)) throw new Error('请选择 .csv 格式的考核记录。');
    if (csv.size > 8 * 1024 * 1024) throw new Error('CSV 文件超过 8 MB，请拆分后再上传。');
    if (!csv.size) throw new Error('CSV 文件为空，请选择含考核记录的文件。');
    const payload = new FormData(); payload.append('file', csv);
    for (const field of ['syllabus', 'rubric']) { const file = form.elements[field].files[0]; if (file) { if (!/\.json$/i.test(file.name)) throw new Error(`${field} 配置需为 .json 文件。`); if (file.size > 256 * 1024) throw new Error(`${field} 配置不能超过 256 KB。`); payload.append(field, file); } }
    if (form.elements.class_name.value.trim()) payload.append('class_name', form.elements.class_name.value.trim());
    if (form.elements.goal.value.trim()) payload.append('goal', form.elements.goal.value.trim());
    setBusy(true, '正在校验记录并生成报告…');
    const report = await api('/upload', { method: 'POST', body: payload });
    renderReport(report); await refreshHistory(); location.hash = 'overview'; window.scrollTo({ top: 0, behavior: 'instant' }); toast('报告已生成并保存。请核对数据质量提示。');
  } catch (error) { errorBox.textContent = error.message; errorBox.hidden = false; errorBox.focus(); }
  finally { setBusy(false, ''); }
}
async function downloadFile(path, fallback) {
  try { const response = await fetch(path, { headers: token ? { Authorization: 'Bearer ' + token } : {} }); if (!response.ok) { if (response.status === 401) authPrompt(); throw new Error(`下载失败（${response.status}）${response.status === 401 ? '，请填写有效令牌。' : ''}`); } const url = URL.createObjectURL(await response.blob()); const link = document.createElement('a'); link.href = url; const encoded = response.headers.get('Content-Disposition')?.match(/filename\*=UTF-8''([^;]+)/i); try { link.download = encoded ? decodeURIComponent(encoded[1]) : fallback; } catch { link.download = fallback; } link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); }
  catch (error) { toast(error.message); }
}
function exportReport() { if (state.report) downloadFile('/api/diagnostics/reports/' + encodeURIComponent(state.report.id) + '/export', `${state.report.class_name}_学情诊断.md`); }
function authPrompt() { state.authRequired = true; $('#auth-help').textContent = '服务器需要有效访问令牌才能读取或生成成绩报告，请填写后重新连接。'; $('#token-input').value = token; if (!$('#token-dialog').open) $('#token-dialog').showModal(); }
function renderReview(report) { const review = report.review || { status: 'pending' }; const reviewed = review.status === 'reviewed'; $('#review-status').textContent = reviewed ? '已人工复核' : '待人工复核'; $('#review-status').className = 'tag' + (reviewed ? '' : ' medium'); $('#review-caption').textContent = reviewed ? `${review.reviewer} · ${when(review.reviewed_at)}${review.note ? ' · ' + review.note : ''}` : '诊断与分层仅为教学建议，需教师核对证据；不自动用于处罚或正式评价。'; $('#open-review').textContent = reviewed ? '查看复核记录' : '人工复核'; }
function openReview() { if (!state.report) return; const review = state.report.review || {}; const form = $('#review-form'); form.elements.reviewer.value = review.reviewer || ''; form.elements.note.value = review.note || ''; $('#reset-review').hidden = review.status !== 'reviewed'; $('#review-error').hidden = true; $('#audit-log').innerHTML = (state.report.audit_log || []).map(item => `<li>${esc(when(item.created_at))} · ${esc({ created: '生成报告', reviewed: '标记已复核', review_reset: '撤回至待复核', agent_run: '运行 Agent' }[item.action] || item.action)}${item.reviewer ? ' · ' + esc(item.reviewer) : ''}${item.note ? ' · ' + esc(item.note) : ''}</li>`).join('') || '<li>暂无操作记录。</li>'; $('#review-dialog').showModal(); }
async function saveReview(reviewed) { const form = $('#review-form'); if (!form.reportValidity()) return; $('#save-review').disabled = true; $('#reset-review').disabled = true; $('#review-error').hidden = true; try { const report = await api('/reports/' + encodeURIComponent(state.report.id) + '/review', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ reviewer: form.elements.reviewer.value.trim(), note: form.elements.note.value.trim(), reviewed, agent_run_id: state.report.agent?.id || null }) }); renderReport(report); $('#review-dialog').close(); await refreshHistory(); toast(reviewed ? '已保存人工复核记录。' : '报告已撤回至待复核。'); } catch (error) { $('#review-error').textContent = error.message; $('#review-error').hidden = false; } finally { $('#save-review').disabled = false; $('#reset-review').disabled = false; } }
async function boot() {
  $('#startup-error').hidden = true;
  try { const auth = await fetch('/api/auth/check'); if (auth.ok) state.authRequired = (await auth.json()).required; } catch { /* 后续业务请求会显示连接错误。 */ }
  if (state.authRequired && !token) { authPrompt(); setConnection(false); return; }
  const results = await Promise.allSettled([api('/schema'), api('/reports')]);
  const failures = [];
  if (results[0].status === 'fulfilled') renderSchema(results[0].value); else failures.push('评分配置：' + results[0].reason.message);
  if (results[1].status === 'fulfilled') { state.reports = results[1].value.reports; renderHistory(); if (state.reports.length && !state.report) { try { renderReport(await api('/reports/' + encodeURIComponent(state.reports[0].id))); } catch (error) { failures.push('报告：' + error.message); } } } else failures.push('历史报告：' + results[1].reason.message);
  setConnection(!failures.length);
  if (failures.length) { const box = $('#startup-error'); box.innerHTML = `${esc(failures.join('；'))}<br><button class="btn" id="retry-boot">重新连接</button>`; box.hidden = false; }
}
window.addEventListener('hashchange', navigate);
document.addEventListener('click', event => {
  const download = event.target.closest('a[href^="/api/diagnostics/sample"]');
  if (download) { event.preventDefault(); const kind = new URL(download.href).searchParams.get('kind') || 'csv'; downloadFile(download.getAttribute('href'), kind === 'csv' ? 'sample.csv' : kind + '.json'); return; }
  const button = event.target.closest('button');
  if (!button) return;
  if (button.hasAttribute('data-demo')) createDemo();
  if (button.dataset.student !== undefined) showStudent(button.dataset.student);
  if (button.dataset.topic !== undefined) showTopic(Number(button.dataset.topic));
  if (button.dataset.report) openReport(button.dataset.report).catch(error => toast(error.message));
  if (button.dataset.close) $('#' + button.dataset.close).close();
  if (button.id === 'retry-boot') boot();
});
$('#upload-form').addEventListener('submit', upload);
$('#agent-form').addEventListener('submit', runDiagnosticAgent);
$('#student-search').addEventListener('input', renderStudents);
$('#student-level').addEventListener('change', renderStudents);
$('#history-search').addEventListener('input', renderHistory);
$('#refresh-history').addEventListener('click', async event => { event.currentTarget.disabled = true; try { await refreshHistory(); toast('报告列表已更新。'); } catch (error) { toast(error.message); } finally { $('#refresh-history').disabled = false; } });
$('#export-report').addEventListener('click', exportReport);
$('#token-settings').addEventListener('click', () => { $('#token-input').value = token; $('#token-dialog').showModal(); });
$('#token-form').addEventListener('submit', event => { event.preventDefault(); token = $('#token-input').value.trim(); if (token) sessionStorage.setItem('eduforge_diagnostics_token', token); else sessionStorage.removeItem('eduforge_diagnostics_token'); $('#token-dialog').close(); boot(); });
$('#clear-token').addEventListener('click', () => { token = ''; sessionStorage.removeItem('eduforge_diagnostics_token'); $('#token-input').value = ''; $('#token-dialog').close(); toast('已清除当前标签页的令牌。'); if (state.authRequired) { state.report = null; $('#report-content').hidden = true; $('#overview-empty').hidden = false; $('#export-report').hidden = true; state.reports = []; renderStudents(); renderHistory(); setConnection(false); } });
$('#open-review').addEventListener('click', openReview);
$('#review-form').addEventListener('submit', event => { event.preventDefault(); saveReview(true); });
$('#reset-review').addEventListener('click', () => saveReview(false));
navigate(); renderStudents(); renderHistory(); boot();
