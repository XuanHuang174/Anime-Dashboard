import { Component } from 'react'

// An unexpected resource render failure must not unmount the recommendation page.
export default class RecommendationResourceBoundary extends Component {
  state = { failed: false }
  static getDerivedStateFromError() { return { failed: true } }
  render() {
    if (!this.state.failed) return this.props.children
    return <div role="alert"><p>资源区暂不可用，推荐和手账记录不受影响。</p>
      <button type="button" className="rec-button" onClick={() => this.setState({ failed: false })}>重试资源区</button></div>
  }
}
