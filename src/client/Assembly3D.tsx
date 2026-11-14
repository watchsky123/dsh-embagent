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

import { useEffect, useRef, useState } from 'react'
import {
  AmbientLight, BoxGeometry, BufferGeometry, CatmullRomCurve3, Color, CylinderGeometry,
  DirectionalLight, DoubleSide, EdgesGeometry, ExtrudeGeometry, GridHelper, Group,
  HemisphereLight, LineBasicMaterial, LineSegments, Mesh, MeshStandardMaterial,
  PerspectiveCamera, Plane, Raycaster, Scene, Shape, TubeGeometry,
  Vector2, Vector3, WebGLRenderer,
} from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import type { Finding, Scene3D as ScenePayload, SnapshotPayload } from '../shared/types.js'

export interface Assembly3DProps {
  readonly snapshot: SnapshotPayload
  readonly selectedRef: string
  readonly findings: readonly Finding[]
  readonly onSelect: (ref: string) => void
}

const SOURCE_COLOR: Record<string, number> = {
  datasheet: 0x2f6f9f,
  measured: 0x2f8f5f,
  derived: 0x4a5f8f,
  estimated: 0x7a5fae,
}

export function Assembly3D(props: Assembly3DProps): JSX.Element {
  const { snapshot, selectedRef, findings, onSelect } = props
  const wrapRef = useRef<HTMLDivElement | null>(null)
  const stateRef = useRef<SceneState | null>(null)
  const [explode, setExplode] = useState(0)
  const [grid, setGrid] = useState(true)
  const [section, setSection] = useState(false)
  const onSelectRef = useRef(onSelect)
  onSelectRef.current = onSelect

  // -- 初始化 -------------------------------------------------------------

  useEffect(() => {
    const wrap = wrapRef.current
    if (wrap === null) return
    const state = createScene(wrap)
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

  useEffect(() => {
    const state = stateRef.current
    if (state === null) return
    state.rebuild(snapshot.scene3d, findings, explode, section, grid)
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
  }, [snapshot.scene3d, findings, explode, section, grid])

  // -- 选中高亮 -----------------------------------------------------------

  useEffect(() => {
    stateRef.current?.highlight(selectedRef)
  }, [selectedRef, snapshot.scene3d])

  return (
    <div className="hwv-canvas-wrap" ref={wrapRef}>
      <div style={{
        position: 'absolute', right: 8, top: 8, display: 'flex', gap: 4, zIndex: 2,
      }}>
        <button className={`hwv-btn${explode > 0 ? ' on' : ''}`}
                onClick={() => { setExplode(explode > 0 ? 0 : 1) }}>
          {explode > 0 ? '合拢' : '爆炸'}
        </button>
        <button className={`hwv-btn${section ? ' on' : ''}`}
                onClick={() => { setSection(!section) }}>剖面</button>
        <button className={`hwv-btn${grid ? ' on' : ''}`}
                onClick={() => { setGrid(!grid) }}>网格</button>
      </div>
      <div className="hwv-legend">
        <span><i style={{ background: '#2f6f9f' }} />数据手册</span>
        <span><i style={{ background: '#7a5fae' }} />估算</span>
        <span><i style={{ background: '#f85149' }} />判定冲突</span>
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
  raycaster: Raycaster
  pickables: Mesh[]
  selected: Mesh | null
  baseColors: Map<string, Color>
  canvasSize: { w: number; h: number }
  frame: number
  fitKey: string
  start(): void
  resize(): void
  rebuild(scene: ScenePayload, findings: readonly Finding[],
          explode: number, section: boolean, grid: boolean): void
  highlight(ref: string): void
  pick(event: MouseEvent): string
  dispose(): void
}

function createScene(host: HTMLElement): SceneState {
  const scene = new Scene()
  scene.background = new Color(0x0f1115)

  const camera = new PerspectiveCamera(45, 1, 0.1, 4000)
  camera.position.set(90, 80, 120)
  camera.up.set(0, 1, 0)

  const renderer = new WebGLRenderer({ antialias: true, alpha: false })
  renderer.setPixelRatio(globalThis.devicePixelRatio ?? 1)
  renderer.localClippingEnabled = true
  host.appendChild(renderer.domElement)
  renderer.domElement.style.display = 'block'
  renderer.domElement.style.width = '100%'
  renderer.domElement.style.height = '100%'

  const controls = new OrbitControls(camera, renderer.domElement)
  controls.enableDamping = true
  controls.dampingFactor = 0.12
  controls.screenSpacePanning = true

  scene.add(new AmbientLight(0xffffff, 1.1))
  scene.add(new HemisphereLight(0xbdd7ff, 0x1a1c22, 0.9))
  const key = new DirectionalLight(0xffffff, 1.6)
  key.position.set(120, 220, 160)
  scene.add(key)
  const fill = new DirectionalLight(0x88aaff, 0.5)
  fill.position.set(-140, 90, -120)
  scene.add(fill)

  const root = new Group()
  // 模型坐标 Z 向上 -> three 的 Y 向上
  root.rotation.x = -Math.PI / 2
  scene.add(root)

  const boardGroup = new Group()
  const partsGroup = new Group()
  const wiresGroup = new Group()
  const helperGroup = new Group()
  root.add(boardGroup, partsGroup, wiresGroup, helperGroup)

  const raycaster = new Raycaster()
  const state: SceneState = {
    scene, camera, renderer, controls, root,
    boardGroup, partsGroup, wiresGroup, helperGroup,
    raycaster, pickables: [], selected: null,
    baseColors: new Map(),
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
    rebuild(payload, findings, explode, section, grid) {
      clearGroup(boardGroup)
      clearGroup(partsGroup)
      clearGroup(wiresGroup)
      clearGroup(helperGroup)
      state.pickables = []
      state.selected = null
      state.baseColors.clear()

      const board = payload.board
      const boardColor = 0x1d4d2b
      const thickness = Math.max(board.thickness_mm, 0.4)

      // 板轮廓挤出
      const shape = new Shape()
      board.outline.forEach(([x, y], index) => {
        if (index === 0) shape.moveTo(x, y)
        else shape.lineTo(x, y)
      })
      shape.closePath()
      const slab = new ExtrudeGeometry(shape, { depth: thickness, bevelEnabled: false })
      slab.translate(0, 0, -thickness)
      const boardMesh = new Mesh(slab, new MeshStandardMaterial({
        color: boardColor, roughness: 0.85, metalness: 0.1, side: DoubleSide,
      }))
      boardMesh.userData.ref = ''
      boardGroup.add(boardMesh)
      const slabEdges = new LineSegments(new EdgesGeometry(slab),
        new LineBasicMaterial({ color: 0x4f7f5f }))
      boardGroup.add(slabEdges)

      // 安装孔
      for (const hole of payload.mount_holes) {
        const geometry = new CylinderGeometry(hole.diameter_mm / 2, hole.diameter_mm / 2,
          thickness * 1.6, 18, 1, true)
        geometry.rotateX(Math.PI / 2)
        geometry.translate(hole.x_mm, hole.y_mm, -thickness / 2)
        boardGroup.add(new Mesh(geometry, new MeshStandardMaterial({
          color: 0x0a0c10, side: DoubleSide,
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
        const [x0, y0, z0, x1, y1, z1] = part.bbox_mm
        const w = Math.max(x1 - x0, 0.4)
        const h = Math.max(y1 - y0, 0.4)
        const d = Math.max(z1 - z0, 0.4)
        const geometry = new BoxGeometry(w, h, d)
        const severity = worst.get(part.ref)
        const color = severity === 'error' ? 0x8f2f2f
          : severity === 'warning' ? 0x8f6f2f
            : (SOURCE_COLOR[part.physical_source] ?? 0x4a4f5a)
        const material = new MeshStandardMaterial({
          color, roughness: 0.6, metalness: 0.15,
        })
        const mesh = new Mesh(geometry, material)
        const explodeOffset = explode > 0 ? (index % 4 + 1) * 3.5 : 0
        mesh.position.set((x0 + x1) / 2, (y0 + y1) / 2,
          z0 + d / 2 + explodeOffset)
        mesh.userData.ref = part.ref
        mesh.userData.baseZ = z0 + d / 2
        mesh.userData.baseColor = color
        partsGroup.add(mesh)
        state.pickables.push(mesh)
        state.baseColors.set(part.ref, new Color(color))
        const edges = new LineSegments(new EdgesGeometry(geometry),
          new LineBasicMaterial({
            color: severity === undefined ? 0x9fb3c8 : 0xffb3ae,
            transparent: true, opacity: 0.55,
          }))
        edges.position.copy(mesh.position)
        partsGroup.add(edges)
      })

      // 线缆
      for (const wire of payload.wires) {
        if (wire.path_mm.length < 2) continue
        const points = wire.path_mm.map(([x, y, z]) =>
          new Vector3(x, y, Math.max(z, 0) + 0.6))
        const curve = new CatmullRomCurve3(points, false, 'catmullrom', 0.2)
        const geometry = new TubeGeometry(curve, Math.max(points.length * 8, 16), 0.45, 6, false)
        wiresGroup.add(new Mesh(geometry, new MeshStandardMaterial({
          color: 0x4c9aff, roughness: 0.5,
        })))
      }

      // 外壳（线框 + 半透明内腔）
      if (payload.enclosure !== null) {
        const inner = payload.enclosure.inner_mm
        const wall = payload.enclosure.wall_mm
        if (inner.w > 0 && inner.h > 0 && inner.d > 0) {
          const geometry = new BoxGeometry(inner.w + wall * 2, inner.h + wall * 2,
            inner.d + wall)
          const shell = new Mesh(geometry, new MeshStandardMaterial({
            color: 0x8b93a1, transparent: true, opacity: 0.08,
            side: DoubleSide, depthWrite: false,
          }))
          shell.position.set(inner.w / 2, inner.h / 2, (inner.d - wall) / 2)
          helperGroup.add(shell)
          const edges = new LineSegments(new EdgesGeometry(geometry),
            new LineBasicMaterial({ color: 0xd29922, transparent: true, opacity: 0.6 }))
          edges.position.copy(shell.position)
          helperGroup.add(edges)
        }
      }

      // 地面网格（模型 XY 平面；GridHelper 自己在本地 XZ，根节点已旋转）
      const span = Math.max(board.size_mm.w, board.size_mm.h) * 2.4
      const gridHelper = new GridHelper(span, Math.max(10, Math.round(span / 10)),
        0x2a2f38, 0x1c2027)
      gridHelper.position.set(board.size_mm.w / 2, board.size_mm.h / 2,
        -thickness - 0.05)
      gridHelper.visible = grid
      helperGroup.add(gridHelper)

      // 剖面：全局裁剪平面。世界坐标 Y 向上，裁掉板中心以上（y > 高）的部分。
      // 这是一个**观察工具**，不改模型，也不参与判定。
      renderer.clippingPlanes = section
        ? [new Plane(new Vector3(0, -1, 0), thickness + 1)]
        : []

      // 视角自适应（只在首次或板子尺寸变化时重置）
      const target = new Vector3(board.size_mm.w / 2, 0, -board.size_mm.h / 2)
      const radius = Math.max(board.size_mm.w, board.size_mm.h) * 1.6
      if (state.fitKey !== `${board.size_mm.w}x${board.size_mm.h}`) {
        state.fitKey = `${board.size_mm.w}x${board.size_mm.h}`
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
      material.color.setHex(0x4c9aff)
      material.emissive.setHex(0x14304d)
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
