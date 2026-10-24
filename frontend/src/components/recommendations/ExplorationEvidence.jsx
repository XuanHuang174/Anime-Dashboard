export default function ExplorationEvidence({ item, expanded = false, onReload }) {
  const evidence = item.evidence
  return <div className="rec-explanation">
    <p className="rec-card-reason">✧ {evidence ? item.reason : '探索依据已更新，请重新探索。'}</p>
    <details className="rec-evidence" key={`${item.bangumiId}:${item.evidenceToken || 'stale'}`} open={expanded || undefined}>
      <summary aria-label={item.explorationQuery?.intent === 'tags' ? `为什么命中《${item.title}》？` : `为什么与《${item.title}》相似？`}>{item.explorationQuery?.intent === 'tags' ? '匹配了哪些题材？' : '相似在哪里？'}<span aria-hidden="true">＋</span></summary>
      <div className="rec-evidence-body">{evidence?.kind === 'selected_work' ? <>
        <span className="rec-evidence-label">根据本次参照作品</span><p>参照作品：《{evidence.source.title}》</p>
        <div className="rec-evidence-tags" aria-label="实际共有题材">{evidence.shared_tags.map(tag => <span key={tag}>{tag}</span>)}</div>
        <p className="rec-evidence-footnote">依据作品共有题材寻找关联，不代表个人喜好概率，也不保证剧情、观感或质量相同。不读取个人评分，不修改口味偏好。</p>
      </> : evidence?.kind === 'selected_tags' ? <>
        <span className="rec-evidence-label">根据本次所选题材</span><p>命中 {evidence.shared_tags.length}/{item.explorationQuery.include_tags.length} 个所选题材</p>
        <div className="rec-evidence-tags" aria-label="实际命中题材">{evidence.shared_tags.map(tag => <span key={tag}>{tag}</span>)}</div>
        <p className="rec-evidence-footnote">按目录已有标注匹配，不代表个人喜好概率。排除题材不能保证作品绝无相关内容。本次浏览不修改口味偏好。</p>
      </> : <><p>旧依据已停止展示。</p><button type="button" className="rec-text-button" onClick={onReload}>重新探索</button></>}</div>
    </details>
  </div>
}
