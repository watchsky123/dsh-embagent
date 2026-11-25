/**
 * 客户端数据入口 —— **store 只跟它说话**。
 *
 * 之前 store 直接散调 `WorkbenchFace` 的 30 个方法：签名散在 rpc.ts，
 * 其中一批返回 `Record<string, unknown>`（survey/emit/sim），类型在调用点
 * 丢掉，后端改字段 `tsc` 看不见（AGENTS §3.2 的老形状）。
 *
 * 这一层做两件事：
 * 1. 把**确定有类型的**返回值钉住（snapshot/checks/budget/…）；
 * 2. 把"确实只能是 unknown"的入口集中在这里（emit/sim/survey），
 *    调用方一眼能看到哪几个还没契约，而不是以为整条链路都有类型。
 *
 * 不做缓存、不做重试——那些在 `queries.ts`。
 *
 * @module dsh-embagent/client/api
 */

import type { WorkbenchFace, WorkspacePayload } from './rpc.js'
import type {
  BootstrapPayload,
  BudgetPayload,
  ChecksPayload,
  CrosscheckPayload,
  DeferredPayload,
  DesignDoc,
  EntityProfile,
  EvidencePayload,
  PreflightPayload,
  DiffPayload,
  MaturityPayload,
  AdrPayload,
  HintPayload,
  ExportPayload,
  ProjectSummary,
  SnapshotPayload,
} from '../shared/types.js'

/** 尚无契约的返回（**集中在此**，新增字段时先补类型再说）。 */
export type LoosePayload = Record<string, unknown>

export interface HwApi {
  // -- 兼容层：与 `WorkbenchFace` 同名（store 的既有调用不动）-----------
  //   新代码请用下面的**域命名空间**；这里保留是为了"只换数据入口、不改
  //   25 个调用点"的迁移顺序。
  hint(): Promise<HintPayload>
  bootstrap(): Promise<BootstrapPayload>
  workspace(): Promise<WorkspacePayload>
  setWorkspace(path: string): Promise<WorkspacePayload>
  createWorkspace(parent: string, name: string): Promise<WorkspacePayload>
  pickDirectory(): Promise<string | null>
  projects(): Promise<ProjectSummary[]>
  createProject(id: string, board: string, title: string): Promise<DesignDoc>
  importProject(id: string, path: string, bomPath?: string): Promise<DesignDoc>
  snapshot(id: string): Promise<SnapshotPayload>
  entity(id: string, ref: string): Promise<EntityProfile>
  movePart(id: string, ref: string, xMm: number, yMm: number,
           rotationZ: number, zMm?: number): Promise<DesignDoc>
  addPart(id: string, ref: string, kind: string, role: string,
          xMm: number, yMm: number, rotationZ?: number): Promise<DesignDoc>
  deletePart(id: string, ref: string): Promise<DesignDoc>
  setPart(id: string, ref: string, patch: Record<string, unknown>): Promise<DesignDoc>
  check(id: string, pitchMm?: number): Promise<ChecksPayload>
  budget(id: string): Promise<BudgetPayload>
  setBudgetLimits(id: string, limits: Record<string, number>): Promise<BudgetPayload>
  crosscheck(id: string): Promise<CrosscheckPayload>
  deferred(id: string, action: string, name?: string, value?: string): Promise<DeferredPayload>
  evidence(id: string): Promise<EvidencePayload>
  emitRun(id: string, jobs?: string, route?: string): Promise<LoosePayload>
  emitProgress(id: string): Promise<LoosePayload>
  exportBundle(id: string): Promise<ExportPayload>
  applyReqDraft(id: string, confirm: boolean): Promise<LoosePayload>
  runBlock(id: string, block: string, params?: Record<string, unknown>): Promise<LoosePayload>
  velxioHealth(): Promise<LoosePayload>
  velxioControl(action: 'start' | 'stop' | 'status'): Promise<LoosePayload>
  simStart(id: string, board: string, hex: string): Promise<LoosePayload>
  simRun(id: string, maxInstructions?: number, maxBytes?: number): Promise<LoosePayload>
  simStop(id: string): Promise<LoosePayload>
  simStatus(id: string): Promise<LoosePayload>
  serialRead(id: string): Promise<LoosePayload>
  survey(write?: boolean): Promise<LoosePayload>
  trace(id: string, action?: string): Promise<LoosePayload>
  preflight(id: string): Promise<PreflightPayload>
  readbackDiff(id: string): Promise<DiffPayload>
  maturity(id: string): Promise<MaturityPayload>
  adrList(id: string): Promise<AdrPayload>
  adrValidate(id: string): Promise<AdrPayload>

  // -- 域命名空间（新代码用）---------------------------------------------
  ws: {
    bootstrap(): Promise<BootstrapPayload>
    list(): Promise<WorkspacePayload>
    set(path: string): Promise<WorkspacePayload>
    create(parent: string, name: string): Promise<WorkspacePayload>
    pick(): Promise<string | null>
  }
  prj: {
    list(): Promise<ProjectSummary[]>
    create(id: string, board: string, title: string): Promise<DesignDoc>
    import(id: string, path: string, bomPath?: string): Promise<DesignDoc>
  }
  design: {
    snapshot(id: string): Promise<SnapshotPayload>
    entity(id: string, ref: string): Promise<EntityProfile>
    move(id: string, ref: string, xMm: number, yMm: number,
         rotationZ: number, zMm?: number): Promise<DesignDoc>
    add(id: string, ref: string, kind: string, role: string,
        xMm: number, yMm: number, rotationZ?: number): Promise<DesignDoc>
    remove(id: string, ref: string): Promise<DesignDoc>
    setPart(id: string, ref: string, patch: Record<string, unknown>): Promise<DesignDoc>
  }
  verify: {
    check(id: string, pitchMm?: number): Promise<ChecksPayload>
    budget(id: string): Promise<BudgetPayload>
    setLimits(id: string, limits: Record<string, number>): Promise<BudgetPayload>
    crosscheck(id: string): Promise<CrosscheckPayload>
    deferred(id: string, action: string, name?: string, value?: string): Promise<DeferredPayload>
    evidence(id: string): Promise<EvidencePayload>
  }
  make: {
    emit(id: string, jobs?: string, route?: string): Promise<LoosePayload>
    progress(id: string): Promise<LoosePayload>
    exportBundle(id: string): Promise<ExportPayload>
  }
  req: { apply(id: string, confirm: boolean): Promise<LoosePayload> }
  decisions: { lock(id: string, name: string, value: string): Promise<DeferredPayload> }
  blocks: { run(id: string, block: string, params?: Record<string, unknown>): Promise<LoosePayload> }
  velxio: {
    health(): Promise<LoosePayload>
    control(action: 'start' | 'stop' | 'status'): Promise<LoosePayload>
  }
  sim: {
    start(id: string, board: string, hex: string): Promise<LoosePayload>
    run(id: string, maxInstructions?: number, maxBytes?: number): Promise<LoosePayload>
    stop(id: string): Promise<LoosePayload>
    status(id: string): Promise<LoosePayload>
    serial(id: string): Promise<LoosePayload>
  }
  survey(write?: boolean): Promise<LoosePayload>
  trace(id: string, action?: string): Promise<LoosePayload>
}

/** 由 `WorkbenchFace` 造一个 api。**只做转发**，不藏行为。 */
export function createApi(face: WorkbenchFace): HwApi {
  return {
    hint: () => face.hint(),
    bootstrap: () => face.bootstrap(),
    workspace: () => face.workspace(),
    setWorkspace: (path) => face.setWorkspace(path),
    createWorkspace: (parent, name) => face.createWorkspace(parent, name),
    pickDirectory: () => face.pickDirectory(),
    projects: () => face.projects(),
    createProject: (id, board, title) => face.createProject(id, board, title),
    importProject: (id, path, bomPath) => face.importProject(id, path, bomPath),
    snapshot: (id) => face.snapshot(id),
    entity: (id, ref) => face.entity(id, ref),
    movePart: (id, ref, x, y, rz, z) => face.movePart(id, ref, x, y, rz, z),
    addPart: (id, ref, kind, role, x, y, rz) => face.addPart(id, ref, kind, role, x, y, rz),
    deletePart: (id, ref) => face.deletePart(id, ref),
    setPart: (id, ref, patch) => face.setPart(id, ref, patch),
    check: (id, pitchMm) => face.check(id, pitchMm),
    budget: (id) => face.budget(id),
    setBudgetLimits: (id, limits) => face.setBudgetLimits(id, limits),
    crosscheck: (id) => face.crosscheck(id),
    deferred: (id, action, name, value) => face.deferred(id, action, name, value),
    evidence: (id) => face.evidence(id),
    emitRun: (id, jobs, route) => face.emitRun(id, jobs, route),
    emitProgress: (id) => face.emitProgress(id),
    exportBundle: (id) => face.exportBundle(id),
    applyReqDraft: (id, confirm) => face.applyReqDraft(id, confirm),
    runBlock: (id, block, params) => face.runBlock(id, block, params),
    velxioHealth: () => face.velxioHealth(),
    velxioControl: (action) => face.velxioControl(action),
    simStart: (id, board, hex) => face.simStart(id, board, hex),
    simRun: (id, mi, mb) => face.simRun(id, mi, mb),
    simStop: (id) => face.simStop(id),
    simStatus: (id) => face.simStatus(id),
    serialRead: (id) => face.serialRead(id),
    survey: (write) => face.survey(write),
    trace: (id, action) => face.trace(id, action),
    preflight: (id) => face.preflight(id),
    readbackDiff: (id) => face.readbackDiff(id),
    maturity: (id) => face.maturity(id),
    adrList: (id) => face.adrList(id),
    adrValidate: (id) => face.adrValidate(id),
    ws: {
      bootstrap: () => face.bootstrap(),
      list: () => face.workspace(),
      set: (path) => face.setWorkspace(path),
      create: (parent, name) => face.createWorkspace(parent, name),
      pick: () => face.pickDirectory(),
    },
    prj: {
      list: () => face.projects(),
      create: (id, board, title) => face.createProject(id, board, title),
      import: (id, path, bomPath) => face.importProject(id, path, bomPath),
    },
    design: {
      snapshot: (id) => face.snapshot(id),
      entity: (id, ref) => face.entity(id, ref),
      move: (id, ref, x, y, rz, z) => face.movePart(id, ref, x, y, rz, z),
      add: (id, ref, kind, role, x, y, rz) => face.addPart(id, ref, kind, role, x, y, rz),
      remove: (id, ref) => face.deletePart(id, ref),
      setPart: (id, ref, patch) => face.setPart(id, ref, patch),
    },
    verify: {
      check: (id, pitchMm) => face.check(id, pitchMm),
      budget: (id) => face.budget(id),
      setLimits: (id, limits) => face.setBudgetLimits(id, limits),
      crosscheck: (id) => face.crosscheck(id),
      deferred: (id, action, name, value) => face.deferred(id, action, name, value),
      evidence: (id) => face.evidence(id),
    },
    make: {
      emit: (id, jobs, route) => face.emitRun(id, jobs, route),
      progress: (id) => face.emitProgress(id),
      exportBundle: (id) => face.exportBundle(id),
    },
    req: { apply: (id, confirm) => face.applyReqDraft(id, confirm) },
    decisions: { lock: (id, name, value) => face.deferred(id, 'lock', name, value) },
    blocks: { run: (id, block, params) => face.runBlock(id, block, params) },
    velxio: {
      health: () => face.velxioHealth(),
      control: (action) => face.velxioControl(action),
    },
    sim: {
      start: (id, board, hex) => face.simStart(id, board, hex),
      run: (id, mi, mb) => face.simRun(id, mi, mb),
      stop: (id) => face.simStop(id),
      status: (id) => face.simStatus(id),
      serial: (id) => face.serialRead(id),
    },
  }
}