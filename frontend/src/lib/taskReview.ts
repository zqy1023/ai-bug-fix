import { object, text } from './taskResults';
import type { ReviewPayload, TaskDetailResponse } from './types';

export function reviewOf(detail?: TaskDetailResponse): ReviewPayload | null {
  if (!detail) return null;
  const step = [...detail.task.steps].reverse().find(item => item.name === 'review_agent');
  const attempt = detail.task.attempt || detail.task.lifecycle?.attempt || 1;
  // A previous report must not masquerade as the current attempt's review.
  if (step && ((step.attempt || 1) < attempt || ['pending', 'running', 'waiting'].includes(step.status))) return null;
  if (!step && attempt > 1) return null;
  const saved = object(detail.task.report.review);
  const output = object(step?.output);
  const current = Object.keys(object(output.review)).length ? object(output.review) : output;
  if (text(current.verdict)) return { ...saved, ...current } as ReviewPayload;
  return Object.keys(saved).length ? saved as ReviewPayload : null;
}

export function isReviewFailed(review?: ReviewPayload | null): boolean {
  return Boolean(review && (text(review.contract_error) || /审查调用失败|未取得有效审查结论|审查调用超时/.test(text(review.summary))));
}

export function reviewTone(value?: string): 'ok' | 'warn' | 'danger' | 'idle' {
  const key = (value || '').toUpperCase();
  if (['PASS', 'PASSED', 'SUFFICIENT'].includes(key)) return 'ok';
  if (['MANUAL', 'UNVERIFIED', 'INSUFFICIENT', 'UNKNOWN'].includes(key)) return 'warn';
  if (['FAIL', 'FAILED'].includes(key)) return 'danger';
  return 'idle';
}

export function reviewLabel(value?: string): string {
  const labels: Record<string, string> = {
    PASS: '通过', PASSED: '通过', FAIL: '未通过', FAILED: '未通过',
    MANUAL: '存在待确认项', UNVERIFIED: '未验证', UNKNOWN: '无法确认',
    SUFFICIENT: '覆盖充分', INSUFFICIENT: '覆盖不足',
  };
  return labels[(value || '').toUpperCase()] || value || '未提供';
}
