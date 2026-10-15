import { Component } from 'react'

/** Keeps a render error from blanking the whole app. `quiet` hides the failed section instead of showing it. */
export default class Boundary extends Component {
  state = { error: null }

  static getDerivedStateFromError(error) { return { error } }

  componentDidCatch(error, info) { console.error('Render error:', error, info?.componentStack) }

  render() {
    const { error } = this.state
    if (!error) return this.props.children
    if (this.props.quiet) return null
    return (
      <div className="callout bad" role="alert" style={{ margin: 24, display: 'grid', gap: 8 }}>
        <b>Something went wrong showing this page.</b>
        <code style={{ fontSize: 12, whiteSpace: 'pre-wrap' }}>{String(error?.message || error)}</code>
        <div><button type="button" className="btn btn-sm" onClick={() => window.location.reload()}>Reload</button></div>
      </div>
    )
  }
}
