/**
 * Group BA validation findings (failed_checks & warnings) by section
 * in dependency order and build targeted fix prompts for the selected section.
 */

export function groupValidationFindings(lastValidation, sections) {
  const empty = {
    isFailed: false,
    failedCount: 0,
    warningCount: 0,
    bySection: new Map(),
    errorSections: [],
    warningSections: [],
  };
  if (!lastValidation || typeof lastValidation !== 'object') return empty;

  const isFailed = String(lastValidation.status || '').toLowerCase() === 'failed';
  const failedChecks = Array.isArray(lastValidation.failed_checks) ? lastValidation.failed_checks : [];
  const warnings = Array.isArray(lastValidation.warnings) ? lastValidation.warnings : [];

  const bySection = new Map();
  sections.forEach((sec, idx) => {
    bySection.set(sec.id, { sec, index: idx, errors: [], warnings: [] });
  });

  const fallbackSecId = bySection.has('workflow') ? 'workflow' : (sections[0] && sections[0].id) || '';

  const assign = (item, kind) => {
    if (!item || typeof item !== 'object') return;
    const targets = Array.isArray(item.affected_sections) && item.affected_sections.length
      ? item.affected_sections.filter((id) => bySection.has(id))
      : (fallbackSecId ? [fallbackSecId] : []);
    if (!targets.length && fallbackSecId) targets.push(fallbackSecId);
    targets.forEach((secId) => {
      const bucket = bySection.get(secId);
      if (bucket) bucket[kind].push(item);
    });
  };

  failedChecks.forEach((item) => assign(item, 'errors'));
  warnings.forEach((item) => assign(item, 'warnings'));

  const errorSections = Array.from(bySection.values()).filter((entry) => entry.errors.length > 0);
  const warningSections = Array.from(bySection.values()).filter((entry) => entry.warnings.length > 0);

  return {
    isFailed,
    failedCount: failedChecks.length,
    warningCount: warnings.length,
    bySection,
    errorSections,
    warningSections,
  };
}

export function buildAgentFixPrompt(sec, errors) {
  const lines = (errors || []).map((err, i) => {
    const id = err.check_id ? `[${err.check_id}] ` : '';
    const detail = String(err.detail || err.name || '').trim();
    const suggestion = String(err.suggestion || '').trim();
    return `${i + 1}. ${id}${detail}${suggestion ? `\n   Gợi ý sửa: ${suggestion}` : ''}`;
  });
  return (
    `Hãy sửa dứt điểm các lỗi Validation sau ở section ${sec.num} (${sec.title}) ` +
    `và cập nhật trực tiếp vào artifact:\n${lines.join('\n')}`
  );
}
