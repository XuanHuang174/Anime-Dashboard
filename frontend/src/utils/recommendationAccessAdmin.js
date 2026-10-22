// Presentation and input checks only; authority is ALWAYS rechecked server-side.
export const isSelfApproval = (selected, action) => action === 'approve' && selected?.is_self === true

export function accessApprovalPayload(selected, action, { reason, days, message = '', confirmed, selfApprovalConfirmed }) {
  if (!selected?.ticket || !['approve', 'reject', 'revoke'].includes(action)) throw new Error('请重新读取审批详情。')
  if (confirmed !== true) throw new Error('请先确认本次审批操作。')
  if (typeof reason !== 'string' || !reason.trim() || reason.trim().length > 200) throw new Error('请填写本次操作原因，最多 200 字。')
  const duration = Number(days)
  if (!Number.isInteger(duration) || duration < 1 || duration > 30) throw new Error('有效天数须为 1–30 的整数。')
  const self = isSelfApproval(selected, action)
  if (self && (selected.self_approval_allowed !== true || selfApprovalConfirmed !== true)) {
    throw new Error('本人授权须额外勾选确认，并将记录为超级管理员自审。')
  }
  return { ticket: selected.ticket, action, reason: reason.trim(), days: duration, public_message: message, confirmed: true,
    ...(self ? { self_approval_confirmed: true } : {}) }
}

export function accessAdminFailure(failure) {
  const status = failure?.response?.status, data = failure?.response?.data
  if (status === 409) return '版本或审批凭据已变化。请重新读取，不会覆盖较新的决定。'
  if (data?.code === 'self_approval_confirmation_required') return '本人授权须额外勾选确认，并将记录为超级管理员自审。'
  if (data?.detail === '不能审批自己的申请。') return '当前后端仍使用禁止本人审批的旧规则，请确认后端已更新后重新读取。'
  if (status === 401 || status === 403) return '管理权限或登录状态已失效，请重新登录并读取；只有当前有效的超级管理员可以审批。'
  if (status === 400) return '审批输入未通过校验，请检查确认勾选、原因及有效天数（1–30）。'
  return '操作未被接受，请重新读取后核对。'
}
