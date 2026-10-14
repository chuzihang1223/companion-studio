import { WorkspaceAutoScrollEngine } from './WorkspaceAutoScrollEngine';

export type ResizeFrame = {
  dx: number;
  dy: number;
  scrollTop: number;
  scrollDelta: number;
  engine: WorkspaceAutoScrollEngine;
};
type Options = {
  id: string;
  dividerId: string | null;
  edge: string;
  layoutVersion: number;
  pointerId: number;
  handle: HTMLElement;
  container: HTMLElement;
  grid: HTMLElement;
  clientX: number;
  clientY: number;
  startSize: { width: number; height: number };
  update: (frame: ResizeFrame) => { scrollTop: number; changed: boolean };
};
const summary = (values: number[]) => {
  const sorted = [...values].sort((a, b) => a - b);
  return {
    count: values.length,
    p50: sorted[Math.floor(sorted.length * 0.5)] || 0,
    p95: sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * 0.95))] || 0,
    max: sorted.at(-1) || 0,
  };
};

/** 一次指针手势拥有一个 rAF 调度器；每帧不改 React 状态、不保存磁盘。 */
export class ResizeSessionController {
  readonly engine: WorkspaceAutoScrollEngine;
  readonly started = performance.now();
  readonly origin: { x: number; y: number };
  clientX: number;
  clientY: number;
  autoScrolling = false;
  private frame = 0;
  private stopped = false;
  private dirty = true;
  private lastTime = 0;
  private frameDurations: number[] = [];
  private frameIntervals: number[] = [];
  private moves = 0;
  private scrollFrames = 0;
  private oldAnchor: string;
  private oldBehavior: string;
  constructor(readonly options: Options) {
    this.engine = new WorkspaceAutoScrollEngine(options.container, options.grid, options.edge);
    this.clientX = options.clientX;
    this.clientY = options.clientY;
    this.origin = {
      x: this.clientX - this.engine.left + options.container.scrollLeft,
      y: this.clientY - this.engine.top + options.container.scrollTop,
    };
    this.oldAnchor = options.container.style.overflowAnchor;
    this.oldBehavior = options.container.style.scrollBehavior;
    options.container.style.overflowAnchor = 'none';
    options.container.style.scrollBehavior = 'auto';
    options.container.scrollTop = options.container.scrollTop; // 中止消息触发但尚未完成的平滑滚动。
    this.lastTime = performance.now();
    this.schedule();
  }
  pointer(x: number, y: number) {
    if (this.stopped) return;
    this.clientX = x;
    this.clientY = y;
    this.dirty = true;
    this.moves++;
    this.schedule();
  }
  invalidateGeometry() {
    this.engine.invalidate();
    this.dirty = true;
    this.schedule();
  }
  private schedule() {
    if (!this.stopped && !this.frame) this.frame = requestAnimationFrame(this.tick);
  }
  private tick = (time: number) => {
    this.frame = 0;
    if (this.stopped) return;
    const dt = Math.min(50, Math.max(0, time - this.lastTime));
    this.lastTime = time;
    const velocity = this.engine.velocity(this.clientY);
    if (this.dirty || velocity) {
      this.frameIntervals.push(dt);
      if (this.frameIntervals.length > 360) this.frameIntervals.shift();
      this.apply((velocity * dt) / 1000);
    }
    if (!this.stopped && (this.dirty || this.autoScrolling)) this.schedule();
  };
  // update 先求允许的几何并绘制，返回后仅写一次滚动，顺序不可随意交换。
  private apply(delta: number) {
    const start = performance.now(),
      { container } = this.options;
    this.engine.measure();
    const before = container.scrollTop,
      y = this.engine.velocity(this.clientY) ? this.engine.pointerY(this.clientY) : this.clientY;
    const result = this.options.update({
      dx: this.clientX - this.engine.left + container.scrollLeft - this.origin.x,
      dy: y - this.engine.top + before - this.origin.y,
      scrollTop: before,
      scrollDelta: delta,
      engine: this.engine,
    });
    if (Math.abs(result.scrollTop - before) > 0.01) container.scrollTop = result.scrollTop;
    this.autoScrolling = !!delta && (result.changed || Math.abs(result.scrollTop - before) > 0.01);
    if (this.autoScrolling) this.scrollFrames++;
    this.dirty = false;
    this.frameDurations.push(performance.now() - start);
    if (this.frameDurations.length > 360) this.frameDurations.shift();
  }
  flush(x: number, y: number) {
    if (this.stopped) return;
    this.clientX = x;
    this.clientY = y;
    cancelAnimationFrame(this.frame);
    this.frame = 0;
    this.apply(0);
  }
  /** 取消、松开和卸载共用幂等清理，恢复手势前的滚动样式并释放捕获。 */
  stop() {
    if (this.stopped) return;
    this.stopped = true;
    cancelAnimationFrame(this.frame);
    this.frame = 0;
    this.autoScrolling = false;
    const { container, handle, pointerId } = this.options;
    container.style.overflowAnchor = this.oldAnchor;
    container.style.scrollBehavior = this.oldBehavior;
    if (handle.hasPointerCapture(pointerId)) handle.releasePointerCapture(pointerId);
  }
  metrics(reason: string) {
    return {
      version: '8.9',
      event: 'resize_session',
      reason,
      module: this.options.id,
      divider: this.options.dividerId,
      edge: this.options.edge,
      layoutVersion: this.options.layoutVersion,
      startSize: this.options.startSize,
      elapsedMs: performance.now() - this.started,
      pointerMoves: this.moves,
      scrollFrames: this.scrollFrames,
      updateMs: summary(this.frameDurations),
      frameIntervalMs: summary(this.frameIntervals),
    };
  }
}
