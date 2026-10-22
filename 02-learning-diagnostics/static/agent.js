/* 当前报告的 Agent 方案与实际工具记录；所有模型内容按文本转义。 */
const agentStatuses = { completed: '已生成 · 请复核', failed: '运行失败', limited: '达到运行上限', not_configured: '尚未配置 API', not_run: '尚未运行', disabled: 'Agent 已关闭' };
const agentToolNames = { get_class_summary: '班级汇总', get_rubric_and_progress: '评分与课程进度', list_topics: '知识点目录', list_students: '学生目录', inspect_topic: '知识点证据', inspect_student: '个人证据', get_exercise_examples: '题库参考', validate_plan: '方案自检' };
function agentRefs(ids) { return (ids || []).map(id => `<a class="text-link" href="#agent-evidence-${esc(id)}">${esc(id)}</a>`).join('、'); }
function renderAgentExercise(item) {
  return `<article class="exercise"><div class="exercise-meta"><strong>${esc(item.topic)} · ${levels[item.level] || esc(item.level)}</strong><span class="quiet">模型生成 · ${item.review_status === 'reviewed' ? '已人工复核' : '待人工复核'}</span></div><p>${esc(item.question)}</p><p class="quiet">依据：${agentRefs(item.evidence_ids)}${item.student_ids.length ? ' · 学号：' + esc(item.student_ids.join('、')) : ' · 供该层级参考'}</p><details><summary>提示与参考答案</summary><p>${esc(item.hint)}</p><p>${esc(item.answer)}</p></details></article>`;
}
function renderAgent(report) {
  const run = report.agent || { status: 'not_run', events: [] }, plan = run.result;
  const available = !!(state.schema?.agent?.enabled && state.schema?.agent?.configured);
  $('#agent-status').textContent = run.review?.status === 'reviewed' && run.status === 'completed' ? '已人工复核' : agentStatuses[run.status] || run.status;
  $('#agent-goal').value = run.goal || $('#agent-goal').value;
  $('#run-agent').disabled = state.loading || !available;
  $('#run-agent').textContent = run.id ? '重新运行 Agent' : '运行 Agent';
  $('#agent-message').textContent = !available ? '当前为基础统计预览。按项目 README 配置回答 API 后重启服务，即可生成 Agent 方案。' : run.status === 'completed' ? `${run.steps} 次模型决策，${run.tool_calls} 次工具调用；方案只针对本次报告和已查看的证据。` : run.id ? `本次运行未发布方案，停止原因：${run.error || run.status}。可修改目标后重试。` : '可以针对这份历史报告运行 Agent。';
  $('#agent-result').hidden = !plan;
  $('#agent-result').innerHTML = plan ? `<p class="agent-summary">${esc(plan.summary)} <span class="quiet">依据：${agentRefs(plan.summary_evidence_ids)}</span></p><div class="agent-columns"><section><h3>发现与依据</h3>${plan.findings.length ? plan.findings.map(item => `<article class="agent-item"><strong>${esc(item.topic)}${item.student_id ? ' · ' + esc(item.student_id) : ''}</strong><p>${esc(item.statement)}</p><p>${agentRefs(item.evidence_ids)}</p></article>`).join('') : '<p class="quiet">现有数据不足，暂不形成学情结论。</p>'}</section><section><h3>补讲安排</h3>${plan.reteaching.length ? plan.reteaching.map(item => `<article class="agent-item"><strong>${esc(item.topic)}</strong><p>${esc(item.reason)}</p><ol>${item.actions.map(action => `<li>${esc(action)}</li>`).join('')}</ol><p>依据：${agentRefs(item.evidence_ids)}</p></article>`).join('') : '<p class="quiet">本次没有提出补讲安排。</p>'}</section></div><section class="agent-exercises"><h3>生成的分层练习</h3>${plan.exercises.length ? plan.exercises.map(renderAgentExercise).join('') : '<p class="quiet">暂无可生成练习的有效证据。</p>'}</section><section class="agent-missing"><h3>尚需核对</h3>${evidenceList(plan.missing_data, '仍需教师核对原题和参考答案。')}</section><details class="agent-evidence"><summary>查看引用的工具证据</summary>${Object.entries(plan.evidence || {}).map(([id, item]) => `<details id="agent-evidence-${esc(id)}"><summary>${esc(id)} · ${esc(item.topic || ({ summary: '班级汇总', progress: '评分与进度', student: '个人证据' }[item.kind]) || item.kind)}</summary><pre>${esc(JSON.stringify(item.data, null, 2))}</pre></details>`).join('')}</details>` : '';
  $('#agent-trace-label').textContent = `工具调用记录 · ${run.tool_calls || 0} 次`;
  $('#agent-trace').innerHTML = (run.events || []).map(event => `<details><summary>${event.kind === 'tool' ? esc(agentToolNames[event.tool] || event.tool) : '方案核验'} · ${event.status === 'ok' ? '通过' : '需调整'}</summary><pre>${esc(JSON.stringify(event.kind === 'tool' ? { arguments: event.arguments, observation: event.observation } : { reason: event.reason || 'passed' }, null, 2))}</pre></details>`).join('') || '<p class="quiet">尚无工具调用记录。</p>';
}
async function runDiagnosticAgent(event) {
  event.preventDefault();
  if (state.loading || !state.report || !$('#agent-form').reportValidity()) return;
  const reportId = state.report.id, goal = $('#agent-goal').value.trim();
  setBusy(true, 'Agent 正在查询证据并生成方案…');
  $('#run-agent').disabled = true;
  $('#agent-message').textContent = 'Agent 正在查询本报告的统计、课程进度与证据，请稍候…';
  try {
    const run = await api('/reports/' + encodeURIComponent(reportId) + '/agent', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ goal }) });
    if (state.report?.id === reportId) renderReport(await api('/reports/' + encodeURIComponent(reportId)));
    await refreshHistory();
    toast(run.status === 'completed' ? '已生成新方案，请重新核对并复核。' : 'Agent 未完成，已保存运行记录。');
  } catch (error) { $('#agent-message').textContent = '运行请求失败：' + error.message; }
  finally { setBusy(false, ''); $('#run-agent').disabled = !(state.schema?.agent?.enabled && state.schema?.agent?.configured); }
}
