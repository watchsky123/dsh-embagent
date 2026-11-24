/**
 * 3D 装配视图 —— **物理/机械权威**。
 *
 * 与 hwforge 的体素视图的关键差别：**体素不参与渲染**。体素只做求解索引
 * （重叠/keepout/高度），这里画的是网格：板轮廓挤出、元件实体盒、线缆管道、
 * 外壳线框。这正是 v3 的判断——"简陋"的是渲染，不是模型。
 *
 * 坐标：场景在**模型坐标**（X 东 / Y 北 / Z 上）里搭建，再把根节点绕 X 转
 * −90°，于是模型 Z 变成 three 的 Y（上）。所有几何都用 mm，不缩放。
 *
 * @module dsh-hw-velxio/client/Assembly3D
 */

import { useEffect, useRef } from 'react'
import {
  ACESFilmicToneMapping, AmbientLight, BoxGeometry, BufferGeometry, CatmullRomCurve3,
  Color, CylinderGeometry,
  DirectionalLight, DoubleSide, EdgesGeometry, ExtrudeGeometry, GridHelper, Group,
  HemisphereLight, LineBasicMaterial, LineSegments, Mesh, MeshStandardMaterial,
  PerspectiveCamera, Plane, Raycaster, Scene, Shape, SRGBColorSpace, TubeGeometry,
  Vector2, Vector3, WebGLRenderer,
} from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import type { Finding, Scene3D as ScenePayload, Scene3DBody,
  SnapshotPayload } from '../shared/types.js'
import { partFill, readPalette, useThemeRevision } from './theme.js'
import type { Palette } from './theme.js'

/** 剖面默认切在最高元件的 60% 处。数字是**观察起点**，不是设计参数。 */
const DEFAULT_SECTION_AT = 0.6

export interface Assembly3DProps {
  readonly snapshot: SnapshotPayload
  readonly selectedRef: string
  readonly findings: readonly Finding[]
  readonly onSelect: (ref: string) => void
  /** 三个视图工具是**受控的**：值与 setter 都来自 store。 */
  readonly explode: boolean
  readonly section: boolean
  readonly grid: boolean
  readonly onExplode: (value: boolean) => void
  readonly onSection: (value: boolean) => void
  readonly onGrid: (value: boolean) => void
  /** 剖面高度比例 0..1（乘最高元件顶面）。只在 `section` 为真时生效。 */
  readonly sectionAt?: number
  readonly onSectionAt?: (value: number) => void
}

export function Assembly3D(props: Assembly3DProps): JSX.Element {
  const { snapshot, selectedRef, findings, onSelect,
    explode, section, grid, onExplode, onSection, onGrid,
    sectionAt = DEFAULT_SECTION_AT, onSectionAt } = props
  const wrapRef = useRef<HTMLDivElement | null>(null)
  const stateRef = useRef<SceneState | null>(null)
  const onSelectRef = useRef(onSelect)
  onSelectRef.current = onSelect

  // -- 初始化 -------------------------------------------------------------

  useEffect(() => {
    const wrap = wrapRef.current
    if (wrap === null) return
    const state = createScene(wrap, readPalette(wrap))
    stateRef.current = state
    const observer = new ResizeObserver(() => { state.resize() })
    observer.observe(wrap)
    state.start()
    return () => {
      observer.disconnect()
      state.dispose()
      stateRef.current = null
    }
  }, [])

  // -- 数据 -> 场景 -------------------------------------------------------

  // 最高元件顶面（mm）。滑杆刻度用它换算，避免写死一个上限。
  const tallest = snapshot.scene3d.parts.reduce(
    (top, part) => Math.max(top, part.bbox_mm[5]), 0)
  const themeRev = useThemeRevision()
  useEffect(() => {
    const state = stateRef.current
    if (state === null) return
    // 主题翻转 → 重读调色板并重建材质颜色（three 不跟 CSS 变量）。
    state.rebuild(snapshot.scene3d, findings, explode, section, grid,
      sectionAt, readPalette(wrapRef.current))
    // 点击选中：只有"没有拖动"才算点击，否则每次转视角都会误选
    let downX = 0
    let downY = 0
    const element = state.renderer.domElement
    element.onpointerdown = (event: PointerEvent) => {
      downX = event.clientX
      downY = event.clientY
    }
    element.onclick = (event: MouseEvent) => {
      if (Math.hypot(event.clientX - downX, event.clientY - downY) > 4) return
      const ref = state.pick(event)
      if (ref !== '') onSelectRef.current(ref)
    }
  }, [snapshot.scene3d, findings, explode, section, grid, sectionAt, themeRev])

  // -- 选中高亮 -----------------------------------------------------------

  useEffect(() => {
    stateRef.current?.highlight(selectedRef)
  }, [selectedRef, snapshot.scene3d, themeRev])

  return (
    <div className="hwv-canvas-wrap" ref={wrapRef}>
      <div className="hwv-pane-tools">
        <button className={`hwv-btn${explode ? ' on' : ''}`} aria-pressed={explode}
                title="按层错开元件，看堆叠关系（状态跨视图保留）"
                onClick={() => { onExplode(!explode) }}>
          {explode ? '合拢' : '爆炸'}
        </button>
        <button className={`hwv-btn${section ? ' on' : ''}`} aria-pressed={section}
                onClick={() => { onSection(!section) }}>剖面</button>
        <button className={`hwv-btn${grid ? ' on' : ''}`} aria-pressed={grid}
                onClick={() => { onGrid(!grid) }}>网格</button>
        {section && (
          <label className="hwv-section-slider"
                 title="剖面高度：只影响显示，不改模型，也不参与判定">
            <input type="range" min="0" max="1" step="0.05" value={sectionAt}
                   aria-label="剖面高度"
                   onChange={(event) => {
                     onSectionAt?.(Number(event.currentTarget.value))
                   }} />
            <span>切于 {(Math.max(0.4, tallest * sectionAt)).toFixed(1)} mm</span>
          </label>
        )}
      </div>
      {snapshot.scene3d.parts.length === 0
        && (snapshot.scene3d.bodies ?? []).length === 0 && (
        <div className="hwv-canvas-empty">
          <strong>这个工程有 0 个实体</strong>
          <span>板子在，但上面没有元件、也没有整机实体 —— 不是渲染坏了</span>
        </div>
      )}
      <div className="hwv-legend">
        <span><i className="src-datasheet" />数据手册</span>
        <span><i className="src-estimated" />估算</span>
        <span><i className="src-error" />判定冲突</span>
        <span><i className="face-top" />顶层</span>
        <span><i className="face-bottom" />底层</span>
        <span><i className="body-metal" />电机/机臂</span>
        <span><i className="body-prop" />桨</span>
        <span><i className="body-shell" />外壳</span>
        <span className="hwv-dim">
          mm · 虚框 = 尺寸未定 · 拖拽旋转 · 滚轮缩放 · 点击选中
        </span>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// three.js 场景
// ---------------------------------------------------------------------------

interface SceneState {
  scene: Scene
  camera: PerspectiveCamera
  renderer: WebGLRenderer
  controls: OrbitControls
  root: Group
  boardGroup: Group
  partsGroup: Group
  /** 整机实体（电机/桨/机臂/外壳）：和 PCB 元件分开挂。 */
  bodyGroup: Group
  wiresGroup: Group
  helperGroup: Group
  /** **世界坐标**的辅助几何（地面网格）。不能挂 root：root 绕 X 转了 −90°，
   *  挂上去 GridHelper 的局部 XZ 面会被转成一面竖墙。 */
  worldGroup: Group
  hemi: HemisphereLight
  raycaster: Raycaster
  pickables: Mesh[]
  selected: Mesh | null
  baseColors: Map<string, Color>
  palette: Palette
  canvasSize: { w: number; h: number }
  frame: number
  fitKey: string
  start(): void
  resize(): void
  rebuild(scene: ScenePayload, findings: readonly Finding[],
          explode: boolean, section: boolean, grid: boolean,
          sectionAt: number, palette: Palette): void
  highlight(ref: string): void
  pick(event: MouseEvent): string
  dispose(): void
}

function createScene(host: HTMLElement, palette: Palette): SceneState {
  const scene = new Scene()
  scene.background = new Color(palette.sceneBg)

  const camera = new PerspectiveCamera(45, 1, 0.1, 4000)
  camera.position.set(90, 80, 120)
  camera.up.set(0, 1, 0)

  const renderer = new WebGLRenderer({ antialias: true, alpha: false })
  renderer.setPixelRatio(globalThis.devicePixelRatio ?? 1)
  renderer.localClippingEnabled = true
  // 物理光照 + 色调映射：不设 toneMapping 时 3.45 的总辐照会直接把材质冲到白
  // （lceda-pro 的 pcb3d 用 RGBELoader 环境贴图 + tone mapping，同一个思路）。
  renderer.toneMapping = ACESFilmicToneMapping
  renderer.toneMappingExposure = 1.05
  renderer.outputColorSpace = SRGBColorSpace
  host.appendChild(renderer.domElement)
  renderer.domElement.style.display = 'block'
  renderer.domElement.style.position = 'absolute'
  renderer.domElement.style.inset = '0'
  renderer.domElement.style.width = '100%'
  renderer.domElement.style.height = '100%'

  const controls = new OrbitControls(camera, renderer.domElement)
  controls.enableDamping = true
  controls.dampingFactor = 0.12
  controls.screenSpacePanning = true

  // 三盏灯的总辐照 ≈ 1.9，配合 ACES 才是"看得见体积"的量级。
  // 旧值 1.1 + 0.85 + 1.5 = 3.45 配 NoToneMapping，全都糊成白块。
  scene.add(new AmbientLight(0xffffff, 0.35))
  const hemi = new HemisphereLight(0xffffff, new Color(palette.canvasBg), 0.45)
  scene.add(hemi)
  const key = new DirectionalLight(0xffffff, 1.2)
  key.position.set(120, 220, 160)
  scene.add(key)
  const fill = new DirectionalLight(0xffffff, 0.25)
  fill.position.set(-140, 90, -120)
  scene.add(fill)
  // 背光：把板子背面的轮廓从黑色里拉出来
  const rim = new DirectionalLight(0xffffff, 0.35)
  rim.position.set(-60, -120, -180)
  scene.add(rim)

  const root = new Group()
  // 模型坐标 Z 向上 -> three 的 Y 向上
  root.rotation.x = -Math.PI / 2
  scene.add(root)

  // 世界坐标的辅助层（地面网格）：与 root 同级，不进旋转
  const worldGroup = new Group()
  scene.add(worldGroup)

  const boardGroup = new Group()
  const partsGroup = new Group()
  const bodyGroup = new Group()
  const wiresGroup = new Group()
  const helperGroup = new Group()
  root.add(boardGroup, partsGroup, bodyGroup, wiresGroup, helperGroup)

  const raycaster = new Raycaster()
  const state: SceneState = {
    scene, camera, renderer, controls, root, hemi,
    boardGroup, partsGroup, bodyGroup, wiresGroup, helperGroup, worldGroup,
    raycaster, pickables: [], selected: null,
    baseColors: new Map(),
    palette,
    canvasSize: { w: host.clientWidth, h: host.clientHeight },
    frame: 0,
    fitKey: '',
    start() {
      const loop = (): void => {
        state.frame = requestAnimationFrame(loop)
        controls.update()
        renderer.render(scene, camera)
      }
      loop()
    },
    resize() {
      const w = Math.max(host.clientWidth, 1)
      const h = Math.max(host.clientHeight, 1)
      state.canvasSize = { w, h }
      camera.aspect = w / h
      camera.updateProjectionMatrix()
      renderer.setSize(w, h, false)
    },
    rebuild(payload, findings, explode, section, grid, sectionAt, palette) {
      clearGroup(boardGroup)
      clearGroup(partsGroup)
      clearGroup(bodyGroup)
      clearGroup(wiresGroup)
      clearGroup(helperGroup)
      clearGroup(worldGroup)
      state.pickables = []
      state.selected = null
      state.baseColors.clear()
      state.palette = palette
      // 主题色在场景级也要重涂：背景 + 半球光的"地面"色跟随调色板。
      scene.background = new Color(palette.sceneBg)
      state.hemi.groundColor = new Color(palette.canvasBg)

      const board = payload.board
      const thickness = Math.max(board.thickness_mm, 0.4)

      // 板轮廓挤出：**每块板各挤一块**。
      //
      // 轮廓已经是**拼板坐标**（Python 侧加过偏移），所以这里直接 extrude，
      // 不需要再平移。单板时 `boards` 长度 1、偏移 0 —— 同一条代码路径。
      const boardsToDraw = payload.boards ?? []
      const slabs = boardsToDraw.length > 0
        ? boardsToDraw
        : [{ ...board, x_mm: 0, y_mm: 0, parts: payload.parts.length }]
      for (const one of slabs) {
        if (one.outline.length < 3) continue
        const shape = new Shape()
        one.outline.forEach(([x, y], index) => {
          if (index === 0) shape.moveTo(x, y)
          else shape.lineTo(x, y)
        })
        shape.closePath()
        const t = Math.max(one.thickness_mm, 0.4)
        const slab = new ExtrudeGeometry(shape, { depth: t, bevelEnabled: false })
        slab.translate(0, 0, -t)
        // 板：阻焊绿 + 低金属度 + 高粗糙度（真实 PCB 的哑光感）。
        const boardMesh = new Mesh(slab, new MeshStandardMaterial({
          color: new Color(palette.board3d), roughness: 0.78, metalness: 0.05,
          side: DoubleSide,
        }))
        boardMesh.userData.ref = ''
        boardGroup.add(boardMesh)
        const slabEdges = new LineSegments(new EdgesGeometry(slab),
          new LineBasicMaterial({ color: new Color(palette.board3dEdge) }))
        boardGroup.add(slabEdges)
      }

      // 铜线：真实走线几何，贴在板面上（顶层 +0.02，底层 −thickness−0.02）。
      // 用细长盒子而不是 TubeGeometry —— 走线是直的，盒子更快也更准。
      for (const wire of payload.wires) {
        if (wire.path_mm.length < 2) continue
        const z = wire.layer === 2 ? -thickness - 0.02 : 0.02
        for (let index = 0; index < wire.path_mm.length - 1; index += 1) {
          const from = wire.path_mm[index]
          const to = wire.path_mm[index + 1]
          if (from === undefined || to === undefined) continue
          const [x1, y1] = from
          const [x2, y2] = to
          const dx = x2 - x1
          const dy = y2 - y1
          const length = Math.hypot(dx, dy)
          if (length <= 0.001) continue
          const width = Math.max(wire.width_mm, 0.15)
          const geometry = new BoxGeometry(length, width, 0.04)
          const mesh = new Mesh(geometry, new MeshStandardMaterial({
            color: new Color(palette.trace), roughness: 0.35, metalness: 0.75,
          }))
          mesh.position.set((x1 + x2) / 2, (y1 + y2) / 2, z)
          mesh.rotation.z = Math.atan2(dy, dx)
          wiresGroup.add(mesh)
        }
      }

      // 安装孔
      for (const hole of payload.mount_holes) {
        const geometry = new CylinderGeometry(hole.diameter_mm / 2, hole.diameter_mm / 2,
          thickness * 1.6, 18, 1, true)
        geometry.rotateX(Math.PI / 2)
        geometry.translate(hole.x_mm, hole.y_mm, -thickness / 2)
        boardGroup.add(new Mesh(geometry, new MeshStandardMaterial({
          color: new Color(palette.hole3d), side: DoubleSide,
        })))
      }

      // 元件实体
      const worst = new Map<string, 'error' | 'warning'>()
      for (const finding of findings) {
        if (finding.ok) continue
        for (const ref of finding.refs) {
          const current = worst.get(ref)
          if (finding.severity === 'error') worst.set(ref, 'error')
          else if (current === undefined) worst.set(ref, 'warning')
        }
      }

      payload.parts.forEach((part) => {
        const [x0, y0, z0, x1, y1] = part.bbox_mm
        // 几何用**未旋转**足迹，再按 rotation_z 转一次。bbox_mm 是旋转后的 AABB，
        // 拿它建盒子再转一次就是转两遍（0/90/180/270 会画得一模一样）。
        const w = Math.max(part.size_mm.w, 0.4)
        const h = Math.max(part.size_mm.h, 0.4)
        const d = Math.max(part.size_mm.d, 0.4)
        const geometry = new BoxGeometry(w, h, d)
        const severity = worst.get(part.ref)
        const color = new Color(severity === 'error' ? palette.fillError
          : severity === 'warning' ? palette.fillWarn
            : partFill(palette, part.physical_source))
        const material = new MeshStandardMaterial({
          color, roughness: 0.6, metalness: 0.15,
        })
        const mesh = new Mesh(geometry, material)
        // **底层元件贴板底面**：z 为负。渲染层与判定层用同一套坐标。
        const isBottom = part.side === 'bottom'
        // 爆炸按「装配面 + 高度档」：顶层向上、底层向下。
        // 旧实现 `(index % 4 + 1) * 3.5` 按**数组下标**分组——与装配层次无关，
        // 且对所有件都加正值，底层件（z 为负）看起来像"沉进板里"。
        const explodeOffset = explode
          ? (isBottom ? -1 : 1) * (bandOf(d) + 1) * 4.5
          : 0
        const centerZ = isBottom ? z0 - d / 2 : z0 + d / 2
        mesh.position.set((x0 + x1) / 2, (y0 + y1) / 2, centerZ + explodeOffset)
        // 模型 Z 向上 = 绕 Z 转；root 已把模型 Z 映射成世界 Y，
        // 所以这里正好是绕"竖直轴"转，和 2D 的 rotation_z 同一件事。
        mesh.rotation.z = (part.rotation_z * Math.PI) / 180
        mesh.userData.ref = part.ref
        mesh.userData.baseZ = centerZ
        mesh.userData.baseColor = color.getHex()
        partsGroup.add(mesh)
        state.pickables.push(mesh)
        state.baseColors.set(part.ref, color.clone())
        const edges = new LineSegments(new EdgesGeometry(geometry),
          new LineBasicMaterial({
            color: new Color(severity === undefined ? palette.edge3d : palette.errStroke),
            transparent: true, opacity: 0.55,
          }))
        edges.position.copy(mesh.position)
        edges.rotation.copy(mesh.rotation)
        partsGroup.add(edges)

        // 焊盘：薄金属片贴在元件**贴板面**上。
        // 顶层元件的焊盘在 z = 0（板面），底层的在 z = -thickness。
        const padZ = isBottom ? z0 : z0
        for (const pad of part.pads) {
          const padGeometry = new BoxGeometry(
            Math.max(pad.w_mm, 0.15), Math.max(pad.h_mm, 0.15), 0.12)
          const padMesh = new Mesh(padGeometry, new MeshStandardMaterial({
            color: new Color(palette.pad3d), roughness: 0.32, metalness: 0.85,
          }))
          padMesh.position.set(pad.x_mm, pad.y_mm, padZ + (isBottom ? -0.06 : 0.06))
          padMesh.rotation.z = (pad.rotation_z * Math.PI) / 180
          partsGroup.add(padMesh)
        }
      })

      // 整机实体（电机 / 桨 / 机臂 / 电池 / 外壳）。
      //
      // **规则只有一条**：`known=false` 的只画棱、不画面。给它一个"看起来对"
      // 的实心圆柱，等于告诉用户这个尺寸核对过——那是 AGENTS §0.1 的渲染版。
      for (const body of payload.bodies ?? []) {
        const part = bodyPart(body, palette)
        part.group.position.set(body.transform.x_mm, body.transform.y_mm,
          body.transform.z_mm)
        part.group.rotation.z = (body.transform.rz * Math.PI) / 180
        bodyGroup.add(part.group)
      }

      // 铜线**只画一遍**（上面那一遍贴板细盒）。
      // 这里曾有一遍 CatmullRom 圆管，悬在板面 0.6mm 之上——同一根走线
      // 画两遍：屏幕上是一条亮线叠一个"鼓包"，draw call 也翻倍。

      // 飞线（**不是铜线**）：从网表推导的"应该连成什么样"。
      // 用细管 + 半透明，和铜线明显区分。
      if (payload.ratlines.length > 0) {
        for (const line of payload.ratlines) {
          if (line.path_mm.length < 2) continue
          const points = line.path_mm.map(([x, y, z]) =>
            new Vector3(x, y, Math.max(z, 0) + 0.25))
          const curve = new CatmullRomCurve3(points, false, 'catmullrom', 0.2)
          const geometry = new TubeGeometry(curve, 2, 0.12, 4, false)
          wiresGroup.add(new Mesh(geometry, new MeshStandardMaterial({
            color: new Color(palette.wire3d), roughness: 0.6,
            transparent: true, opacity: 0.45,
          })))
        }
      }

      // 外壳（腔体线框 + 半透明填充 + 盖）。
      //
      // **坐标不能沿用旧假设**：旧代码把"板原点"当成"腔原点"，在
      // `floor_z_mm` / `board_offset_mm` 出现以后必然错位（板偏在一角、
      // 腔体一半埋进地板）。现在的换算：
      //
      //   模型 z=0 是**板顶**；腔底在 z = −(floor + 板厚)；腔顶在 z = inner.d。
      //   腔体 x ∈ [−offset.x, −offset.x + inner.w]，板在 [0, panel.w]。
      //
      // 开口**不在 3D 挖**（要 CSG 或者分段墙板，属 V2 剩余）：2D 已按真实
      // 矩形投影，3D 先只画腔体与盖，别让人以为孔已经开好了。
      if (payload.enclosure !== null) {
        const enc = payload.enclosure
        const floorZ = enc.floor_z_mm + enc.board_thickness_mm
        const cavityH = enc.inner_mm.d + floorZ
        if (enc.inner_mm.w > 0 && enc.inner_mm.h > 0 && cavityH > 0) {
          const geometry = new BoxGeometry(enc.inner_mm.w, enc.inner_mm.h, cavityH)
          const shell = new Mesh(geometry, new MeshStandardMaterial({
            color: new Color(palette.bodyShell), transparent: true, opacity: 0.10,
            side: DoubleSide, depthWrite: false,
          }))
          shell.position.set(
            enc.inner_mm.w / 2 - enc.board_offset_mm.x,
            enc.inner_mm.h / 2 - enc.board_offset_mm.y,
            (enc.inner_mm.d - floorZ) / 2)
          helperGroup.add(shell)
          const edges = new LineSegments(new EdgesGeometry(geometry),
            new LineBasicMaterial({
              color: new Color(palette.warnStroke), transparent: true, opacity: 0.55,
            }))
          edges.position.copy(shell.position)
          helperGroup.add(edges)
        }
        // 盖：单独一块板，厚度来自 `lid_mm`。没有盖厚就不画。
        if (enc.lid_mm > 0 && enc.inner_mm.w > 0 && enc.inner_mm.h > 0) {
          const lid = new Mesh(
            new BoxGeometry(enc.inner_mm.w, enc.inner_mm.h, enc.lid_mm),
            new MeshStandardMaterial({ color: new Color(palette.bodyShell),
              transparent: true, opacity: 0.35, side: DoubleSide }))
          lid.position.set(
            enc.inner_mm.w / 2 - enc.board_offset_mm.x,
            enc.inner_mm.h / 2 - enc.board_offset_mm.y,
            enc.inner_mm.d + enc.lid_mm / 2)
          helperGroup.add(lid)
        }
      }

      // 地面网格与相机取景用**拼板**尺寸，不是主板：
      // 多板工程里只按主板取景，其余板全部在视野外。
      const panelW = payload.panel?.size_mm?.w ?? board.size_mm.w
      const panelH = payload.panel?.size_mm?.h ?? board.size_mm.h
      // 整机实体还要再撑一次取景：5.1 寸桨盘 129.5mm，比 68.6mm 的板宽一倍，
      // 只按板取景会把桨叶切在画面外（V1 第一版截图就是这样）。
      let reach = Math.max(panelW, panelH)
      for (const body of payload.bodies ?? []) {
        const size = Math.max(body.shape.diameter_mm, body.shape.w, body.shape.h)
        reach = Math.max(reach,
          Math.abs(body.transform.x_mm) + size / 2,
          Math.abs(body.transform.y_mm) + size / 2)
      }

      // 地面网格：**世界坐标**。GridHelper 的几何在它自己的局部 XZ 面，
      // 挂到旋转过的 root 下会被 root 的 −90° 转成一面竖直的墙。
      // 世界坐标 = 模型 (x,y,z) → (x, z, −y)，所以地面在 y = −thickness。
      const span = Math.max(panelW, panelH, reach) * 2.4
      const gridHelper = new GridHelper(span, Math.max(10, Math.round(span / 10)),
        new Color(palette.grid3dMajor), new Color(palette.grid3dMinor))
      gridHelper.position.set(panelW / 2, -thickness - 0.05, -panelH / 2)
      gridHelper.visible = grid
      worldGroup.add(gridHelper)

      // 剖面：全局裁剪平面（世界 Y 向上，保留 y < 切面）。
      // 切面高度 = 最高元件顶面 × `sectionAt`（0..1）。
      // 旧实现写死 `thickness + 1`：板顶只到 1.6mm，于是切面永远在 2.6mm，
      // 高元件被整片切掉、矮元件全留——滑杆都没得拉。
      // 这是**观察工具**：不改模型，也不参与判定。
      const tallestTop = payload.parts.reduce(
        (top, part) => Math.max(top, part.bbox_mm[5]), 0)
      const cutZ = Math.max(0.4, tallestTop * sectionAt)
      renderer.clippingPlanes = section
        ? [new Plane(new Vector3(0, -1, 0), cutZ)]
        : []

      // 视角自适应（只在首次或板子尺寸变化时重置）
      const target = new Vector3(panelW / 2, 0, -panelH / 2)
      const radius = Math.max(panelW, panelH, reach) * 1.6
      if (state.fitKey !== `${panelW}x${panelH}x${reach}`) {
        state.fitKey = `${panelW}x${panelH}x${reach}`
        camera.position.set(target.x + radius, target.y + radius * 0.9,
          target.z + radius)
        controls.target.copy(target)
        controls.update()
      }
      state.resize()
    },
    highlight(ref) {
      if (state.selected !== null) {
        const previous = state.selected.userData.ref as string
        const color = state.baseColors.get(previous)
        const material = state.selected.material as MeshStandardMaterial
        if (color !== undefined) material.color.copy(color)
        material.emissive.setHex(0x000000)
        state.selected = null
      }
      const mesh = state.pickables.find((m) => m.userData.ref === ref)
      if (mesh === undefined) return
      const material = mesh.material as MeshStandardMaterial
      material.color.set(state.palette.selStroke)
      material.emissive.set(state.palette.selStroke)
      material.emissive.multiplyScalar(0.25)
      state.selected = mesh
    },
    pick(event) {
      const rect = renderer.domElement.getBoundingClientRect()
      const ndc = new Vector2(
        ((event.clientX - rect.left) / rect.width) * 2 - 1,
        -((event.clientY - rect.top) / rect.height) * 2 + 1,
      )
      raycaster.setFromCamera(ndc, camera)
      const hits = raycaster.intersectObjects(state.pickables, false)
      const first = hits[0]
      if (first === undefined) return ''
      return String(first.object.userData.ref ?? '')
    },
    dispose() {
      cancelAnimationFrame(state.frame)
      controls.dispose()
      renderer.dispose()
      renderer.domElement.remove()
      scene.clear()
    },
  }
  return state
}

/**
 * 高度档：爆炸时同一面内按元件高度分档，避免 43 件各飞各的。
 * 分档边界（1.2 / 3 / 6 mm）对应实测的常见封装高度：0603≈0.5、
 * 排针≈2.5、电解≈5.4、Type-C≈3.2。**这是显示分档，不是判定阈值。**
 */
function bandOf(heightMm: number): number {
  if (heightMm <= 1.2) return 0
  if (heightMm <= 3) return 1
  if (heightMm <= 6) return 2
  return 3
}

/**
 * 一个整机实体的 three 节点。
 *
 * 形状词汇只有四种（见 `BODY_SHAPES`）：`box`/`prism` 盒、`cylinder` 圆柱
 * （电机/电容）、`disc` 盘（桨）、`mesh` 模型文件。**没有加载器时 `mesh`
 * 也只画线框**——仓库里目前没有任何 `.glb`，假装能加载比不画更糟。
 *
 * 坐标：模型 Z 向上（根节点已转 −90°），所以圆柱要先绕 X 转 90° 把轴摆正。
 */
function bodyPart(body: Scene3DBody, palette: Palette): { group: Group } {
  const group = new Group()
  const shape = body.shape
  const color = new Color(body.kind === 'propeller' ? palette.bodyProp
    : body.kind === 'shell' || body.kind === 'lid' ? palette.bodyShell
      : palette.bodyMetal)

  if (!body.known) {
    // 占位框：尺寸只用**已有字段**（哪怕是 0），没有就给一个 8×8×4 的"未知"，
    // 并在图例里说明"虚框 = 尺寸未定"。虚线用棱替代：LineDashed 需要
    // computeLineDistances，不值得为一个占位框引入。
    const w = Math.max(shape.w, shape.diameter_mm, 8)
    const h = Math.max(shape.h, shape.diameter_mm, 8)
    const d = Math.max(shape.d, shape.thickness_mm, 4)
    const geometry = new BoxGeometry(w, h, d)
    const edges = new LineSegments(new EdgesGeometry(geometry),
      new LineBasicMaterial({ color: new Color(palette.warnStroke),
        transparent: true, opacity: 0.85 }))
    edges.position.set(0, 0, d / 2)
    group.add(edges)
    geometry.dispose()
    return { group }
  }

  if (shape.type === 'cylinder') {
    const height = Math.max(shape.d, 0.4)
    const geometry = new CylinderGeometry(shape.diameter_mm / 2,
      shape.diameter_mm / 2, height, 24)
    geometry.rotateX(Math.PI / 2)
    const mesh = new Mesh(geometry, new MeshStandardMaterial({
      color, roughness: 0.45, metalness: 0.55 }))
    mesh.position.set(0, 0, height / 2)
    group.add(mesh)
    return { group }
  }

  if (shape.type === 'disc') {
    const thickness = Math.max(shape.thickness_mm, 0.4)
    const hubR = Math.max(shape.diameter_mm * 0.06, 1)
    const hub = new CylinderGeometry(hubR, hubR, thickness * 1.8, 18)
    hub.rotateX(Math.PI / 2)
    const hubMesh = new Mesh(hub, new MeshStandardMaterial({ color, roughness: 0.5 }))
    hubMesh.position.set(0, 0, thickness * 0.9)
    group.add(hubMesh)
    const blades = Math.max(0, Math.round(shape.blades))
    const bladeLength = Math.max(shape.diameter_mm / 2 - hubR, 1)
    const bladeWidth = Math.max(shape.diameter_mm * 0.13, 2)
    for (let index = 0; index < blades; index += 1) {
      const geometry = new BoxGeometry(bladeLength, bladeWidth, thickness * 0.5)
      const blade = new Mesh(geometry, new MeshStandardMaterial({
        color, roughness: 0.55, metalness: 0.1 }))
      blade.position.set(hubR + bladeLength / 2, 0, thickness * 0.6)
      const pivot = new Group()
      pivot.rotation.z = (index * 2 * Math.PI) / blades
      pivot.add(blade)
      group.add(pivot)
    }
    return { group }
  }

  if (shape.type === 'mesh') {
    // 模型文件没有加载器：只画同尺寸线框，并在图例里说明。
    const w = Math.max(shape.w, 4)
    const h = Math.max(shape.h, 4)
    const d = Math.max(shape.d, 2)
    const geometry = new BoxGeometry(w, h, d)
    const edges = new LineSegments(new EdgesGeometry(geometry),
      new LineBasicMaterial({ color: new Color(palette.warnStroke),
        transparent: true, opacity: 0.55 }))
    edges.position.set(0, 0, d / 2)
    group.add(edges)
    geometry.dispose()
    return { group }
  }

  const w = Math.max(shape.w, 0.4)
  const h = Math.max(shape.h, 0.4)
  const d = Math.max(shape.d, 0.4)
  const mesh = new Mesh(new BoxGeometry(w, h, d), new MeshStandardMaterial({
    color, roughness: 0.6, metalness: 0.2 }))
  mesh.position.set(0, 0, d / 2)
  group.add(mesh)
  return { group }
}

function clearGroup(group: Group): void {
  for (const child of [...group.children]) {
    group.remove(child)
    disposeObject(child)
  }
}

function disposeObject(object: unknown): void {
  const node = object as Mesh | LineSegments
  const geometry = node.geometry as BufferGeometry | undefined
  if (geometry !== undefined && geometry !== null) geometry.dispose()
  const material = (node as Mesh).material
  if (Array.isArray(material)) {
    for (const item of material) item.dispose()
  } else if (material !== undefined && material !== null) {
    ;(material as MeshStandardMaterial).dispose()
  }
}
