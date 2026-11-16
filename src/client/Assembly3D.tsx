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
import type { Finding, Scene3D as ScenePayload, SnapshotPayload } from '../shared/types.js'
import { partFill, readPalette, useThemeRevision } from './theme.js'
import type { Palette } from './theme.js'

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
}

export function Assembly3D(props: Assembly3DProps): JSX.Element {
  const { snapshot, selectedRef, findings, onSelect,
    explode, section, grid, onExplode, onSection, onGrid } = props
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

  const themeRev = useThemeRevision()
  useEffect(() => {
    const state = stateRef.current
    if (state === null) return
    // 主题翻转 → 重读调色板并重建材质颜色（three 不跟 CSS 变量）。
    state.rebuild(snapshot.scene3d, findings, explode, section, grid,
      readPalette(wrapRef.current))
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
  }, [snapshot.scene3d, findings, explode, section, grid, themeRev])

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
      </div>
      {snapshot.scene3d.parts.length === 0 && (
        <div className="hwv-canvas-empty">
          <strong>这个工程有 0 个实体</strong>
          <span>板子在，但上面没有元件 —— 不是渲染坏了</span>
        </div>
      )}
      <div className="hwv-legend">
        <span><i className="src-datasheet" />数据手册</span>
        <span><i className="src-estimated" />估算</span>
        <span><i className="src-error" />判定冲突</span>
        <span><i className="face-top" />顶层</span>
        <span><i className="face-bottom" />底层</span>
        <span className="hwv-dim">mm · 拖拽旋转 · 滚轮缩放 · 点击选中</span>
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
          explode: boolean, section: boolean, grid: boolean, palette: Palette): void
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
  const wiresGroup = new Group()
  const helperGroup = new Group()
  root.add(boardGroup, partsGroup, wiresGroup, helperGroup)

  const raycaster = new Raycaster()
  const state: SceneState = {
    scene, camera, renderer, controls, root, hemi,
    boardGroup, partsGroup, wiresGroup, helperGroup, worldGroup,
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
    rebuild(payload, findings, explode, section, grid, palette) {
      clearGroup(boardGroup)
      clearGroup(partsGroup)
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

      payload.parts.forEach((part, index) => {
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
        const explodeOffset = explode ? (index % 4 + 1) * 3.5 : 0
        // **底层元件贴板底面**：z 为负。渲染层与判定层用同一套坐标。
        const isBottom = part.side === 'bottom'
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

      // 线缆（**铜线**：真实走线几何）
      for (const wire of payload.wires) {
        if (wire.path_mm.length < 2) continue
        const points = wire.path_mm.map(([x, y, z]) =>
          new Vector3(x, y, Math.max(z, 0) + 0.6))
        const curve = new CatmullRomCurve3(points, false, 'catmullrom', 0.2)
        const geometry = new TubeGeometry(curve, Math.max(points.length * 8, 16), 0.45, 6, false)
        wiresGroup.add(new Mesh(geometry, new MeshStandardMaterial({
          color: new Color(palette.wire3d), roughness: 0.5,
        })))
      }

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

      // 外壳（线框 + 半透明内腔）
      if (payload.enclosure !== null) {
        const inner = payload.enclosure.inner_mm
        const wall = payload.enclosure.wall_mm
        if (inner.w > 0 && inner.h > 0 && inner.d > 0) {
          const geometry = new BoxGeometry(inner.w + wall * 2, inner.h + wall * 2,
            inner.d + wall)
          const shell = new Mesh(geometry, new MeshStandardMaterial({
            color: new Color(palette.shell3d), transparent: true, opacity: 0.08,
            side: DoubleSide, depthWrite: false,
          }))
          shell.position.set(inner.w / 2, inner.h / 2, (inner.d - wall) / 2)
          helperGroup.add(shell)
          const edges = new LineSegments(new EdgesGeometry(geometry),
            new LineBasicMaterial({
              color: new Color(palette.warnStroke), transparent: true, opacity: 0.6,
            }))
          edges.position.copy(shell.position)
          helperGroup.add(edges)
        }
      }

      // 地面网格与相机取景用**拼板**尺寸，不是主板：
      // 多板工程里只按主板取景，其余板全部在视野外。
      const panelW = payload.panel?.size_mm?.w ?? board.size_mm.w
      const panelH = payload.panel?.size_mm?.h ?? board.size_mm.h

      // 地面网格：**世界坐标**。GridHelper 的几何在它自己的局部 XZ 面，
      // 挂到旋转过的 root 下会被 root 的 −90° 转成一面竖直的墙。
      // 世界坐标 = 模型 (x,y,z) → (x, z, −y)，所以地面在 y = −thickness。
      const span = Math.max(panelW, panelH) * 2.4
      const gridHelper = new GridHelper(span, Math.max(10, Math.round(span / 10)),
        new Color(palette.grid3dMajor), new Color(palette.grid3dMinor))
      gridHelper.position.set(panelW / 2, -thickness - 0.05, -panelH / 2)
      gridHelper.visible = grid
      worldGroup.add(gridHelper)

      // 剖面：全局裁剪平面。世界坐标 Y 向上，裁掉板中心以上（y > 高）的部分。
      // 这是一个**观察工具**，不改模型，也不参与判定。
      renderer.clippingPlanes = section
        ? [new Plane(new Vector3(0, -1, 0), thickness + 1)]
        : []

      // 视角自适应（只在首次或板子尺寸变化时重置）
      const target = new Vector3(panelW / 2, 0, -panelH / 2)
      const radius = Math.max(panelW, panelH) * 1.6
      if (state.fitKey !== `${panelW}x${panelH}`) {
        state.fitKey = `${panelW}x${panelH}`
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
