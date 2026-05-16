// https://www.reddit.com/r/vscode/comments/11e66xh/i_made_neovide_alike_cursor_effect_on_vscode/

// Configuration

// Set the color of the cursor trail to match the user's cursor color
const Color = "#ffffff" // If set to "default," it will use the theme's cursor color.
// }
// Set the style of the cursor to either a line or block
// line option use fill() to draw trail, block option use lineTo to draw trail
const CursorStyle = "line" // Options are 'line' or 'block'

// Set the length of the cursor trail. A higher value may cause lag.
const TrailLength = 7

// Set the polling rate for handling cursor created and destroyed events, in milliseconds.
const CursorUpdatePollingRate = 200

// Set the whole cursor trail animation speed multiplier.
const AnimationSpeed = 1

// Use shadow
const UseShadow = true
const ShadowColor = Color
const ShadowBlur = 4


// imported from https://github.com/tholman/cursor-effects/blob/master/src/rainbowCursor.js
function createTrail(options) {
  const totalParticles = options?.length || 20
  let particlesColor = options?.color || "#A052FF"
  const style = options?.style || "block"
  const canvas = options?.canvas
  const context = canvas.getContext("2d")
  let cursor = { x: 0, y: 0 }
  let animatedCursor = { x: 0, y: 0 }
  let particles = []
  let width,height
  let sizeX = options?.size || 3
  let sizeY = options?.sizeY || sizeX*2.2
  let cursorsInitted = false

  // update canvas size
  function updateSize(x,y) {
    width = x
    height = y
    canvas.width = x
    canvas.height = y
  }

  // update cursor position
  function move(x,y) {
    x = x + sizeX/2
    cursor.x = x
    cursor.y = y
    if (cursorsInitted === false) {
      cursorsInitted = true
      animatedCursor.x = x
      animatedCursor.y = y
      for (let i = 0; i < totalParticles; i++) {
        addParticle(x, y)
      }
    }
  }

  // particle class
  class Particle {
    constructor(x, y) {
      this.position = { x: x, y: y }
    }
  }

  function addParticle(x, y, image) {
    particles.push(new Particle(x, y, image))
  }

  function colorWithAlpha(color, alpha) {
    const value = color.trim()
    const shortHex = value.match(/^#([0-9a-f])([0-9a-f])([0-9a-f])$/i)
    if (shortHex) {
      const [, r, g, b] = shortHex
      return `rgba(${parseInt(r + r, 16)}, ${parseInt(g + g, 16)}, ${parseInt(b + b, 16)}, ${alpha})`
    }

    const hex = value.match(/^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})?$/i)
    if (hex) {
      const [, r, g, b] = hex
      return `rgba(${parseInt(r, 16)}, ${parseInt(g, 16)}, ${parseInt(b, 16)}, ${alpha})`
    }

    const rgb = value.match(/^rgba?\(([^)]+)\)$/i)
    if (rgb) {
      const [r, g, b] = rgb[1].split(",").map(part => part.trim())
      return `rgba(${r}, ${g}, ${b}, ${alpha})`
    }

    return alpha === 1 ? color : "transparent"
  }

  function createTrailGradient() {
    const head = particles[0]?.position
    const tail = particles[particles.length - 1]?.position
    if (!head || !tail) return particlesColor

    const gradient = context.createLinearGradient(head.x, head.y, tail.x, tail.y)
    gradient.addColorStop(0, colorWithAlpha(particlesColor, 1))
    gradient.addColorStop(1, colorWithAlpha(particlesColor, 0))
    return gradient
  }

  function calculatePosition() {
    const speed = Math.max(0, Math.min(AnimationSpeed, 1))
    const chainSpeedX = 1 - Math.pow(1 - 0.47, speed)
    const chainSpeedY = 1 - Math.pow(1 - 0.52, speed)

    animatedCursor.x += (cursor.x - animatedCursor.x) * speed
    animatedCursor.y += (cursor.y - animatedCursor.y) * speed

    let x = animatedCursor.x,y = animatedCursor.y

    for (const particleIndex in particles) {
      const nextParticlePos = (particles[+particleIndex + 1] || particles[0]).position
      const particlePos = particles[+particleIndex].position

      particlePos.x += (x - particlePos.x) * speed
      particlePos.y += (y - particlePos.y) * speed
      
      x += (nextParticlePos.x - particlePos.x) * chainSpeedX
      y += (nextParticlePos.y - particlePos.y) * chainSpeedY
    }
  }

  // for block cursor
  function drawLines() {
    context.beginPath()
    context.lineJoin = "round"
    context.strokeStyle = createTrailGradient()
    const lineWidth = Math.min(sizeX,sizeY)
    context.lineWidth = lineWidth

    if (UseShadow) {
      context.shadowColor = ShadowColor;
      context.shadowBlur = ShadowBlur;
    }

    // draw 3 lines
    let ymut = (sizeY-lineWidth)/3
    for (let yoffset=0;yoffset<=3;yoffset++) {
      let offset = yoffset*ymut
      for (const particleIndex in particles) {
        const pos = particles[particleIndex].position
        if (particleIndex == 0) {
          context.moveTo(pos.x, pos.y + offset + lineWidth/2)
        } else {
          context.lineTo(pos.x, pos.y + offset + lineWidth/2)
        }
      }
    }
    context.stroke()
  }

  // for line cursor
  function drawPath() {
    context.beginPath()
    context.fillStyle = createTrailGradient()
    if (UseShadow) {
      context.shadowColor = ShadowColor;
      context.shadowBlur = ShadowBlur;
    }

    // draw path
    for (let particleIndex=0;particleIndex<totalParticles;particleIndex++) {
      const pos = particles[+particleIndex].position
      if (particleIndex == 0) {
        context.moveTo(pos.x, pos.y)
      } else {
        context.lineTo(pos.x, pos.y)
      }
    }
    for (let particleIndex=totalParticles-1;particleIndex>=0;particleIndex--) {
      const pos = particles[+particleIndex].position
      context.lineTo(pos.x, pos.y+sizeY)
    }
    context.closePath()
    context.fill()
  }

  function updateParticles() {
    if (!cursorsInitted) return

    context.clearRect(0, 0, width, height)
    calculatePosition()

    if (style=="line") drawPath()
    else if (style=="block") drawLines()
  }

  function updateCursorSize(newSize,newSizeY) {
    sizeX = newSize
    if (newSizeY) sizeY = newSizeY
  }

  return {
    updateParticles: updateParticles,
    move: move,
    updateSize: updateSize,
    updateCursorSize: updateCursorSize
  }
}

// cursor create/remove/move event handler
// by qwreey
// (very dirty but may working)
async function createCursorHandler(handlerFunctions) {
  // Get Editor with dirty way (... due to vscode plugin api's limit)
  /** @type { Element } */
  let editor
  while (!editor) {
    await new Promise(resolve=>setTimeout(resolve, 100))
    editor = document.querySelector(".part.editor")
  }
  handlerFunctions?.onStarted(editor)

  // cursor cache
  let updateHandlers = []
  let cursorId = 0
  let lastObjects = {}
  let lastCursor = 0

  // cursor update handler
  function createCursorUpdateHandler(target,cursorId,cursorHolder,minimap) {
    let lastX,lastY // save last position
    let update = (editorX,editorY)=>{
      // If cursor was destroyed, remove update handler
      if (!lastObjects[cursorId]) {
        updateHandlers.splice(updateHandlers.indexOf(update),1)
        return
      }

      // get cursor position
      let {left:newX,top:newY} = target.getBoundingClientRect()
      let revX = newX-editorX,revY = newY-editorY

      // if have no changes, ignore
      if (revX == lastX && revY == lastY && lastCursor == cursorId) return
      lastX = revX;lastY = revY // update last position

      // wrong position
      if (revX<=0 || revY<=0) return

      // if it is invisible, ignore
      if (target.style.visibility == "hidden") return

      // if moved over minimap, ignore
      if (minimap && minimap.offsetWidth != 0) {
        const { left, right, top, bottom } = minimap.getBoundingClientRect()
        if (newX >= left && newX <= right && newY >= top && newY <= bottom) return
      }

      // if cursor is not displayed on screen, ignore
      if (cursorHolder.getBoundingClientRect().left > newX) return

      // update corsor position
      lastCursor = cursorId
      handlerFunctions?.onCursorPositionUpdated(revX,revY)
      handlerFunctions?.onCursorSizeUpdated(target.clientWidth,target.clientHeight)
    }
    updateHandlers.push(update)
  }

  // handle cursor create/destroy event (using polling, due to event handlers are LAGGY)
  let lastVisibility = "hidden"
  setInterval(async ()=>{
    let now = [],count = 0
    // created
    for (const target of editor.getElementsByClassName("cursor")) {
      if (target.style.visibility != "hidden") count++
      if (target.hasAttribute("cursorId")) {
        now.push(+target.getAttribute("cursorId"))
        continue
      }
      let thisCursorId = cursorId++
      now.push(thisCursorId)
      lastObjects[thisCursorId] = target
      target.setAttribute("cursorId",thisCursorId)
      let cursorHolder = target.parentElement.parentElement.parentElement
      let minimap = cursorHolder.parentElement.querySelector(".minimap")
      createCursorUpdateHandler(target,thisCursorId,cursorHolder,minimap)
      // console.log("DEBUG-CursorCreated",thisCursorId)
    }
    
    // update visible
    let visibility = count<=1 ? "visible" : "hidden"
    if (visibility != lastVisibility) {
      handlerFunctions?.onCursorVisibilityChanged(visibility)
      lastVisibility = visibility
    }

    // destroyed
    for (const id in lastObjects) {
      if (now.includes(+id)) continue
      delete lastObjects[+id]
      // console.log("DEBUG-CursorRemoved",+id)
    }
  },handlerFunctions?.cursorUpdatePollingRate || 500)

  // read cursor position polling
  function updateLoop() {
    let {left:editorX,top:editorY} = editor.getBoundingClientRect()
    for (handler of updateHandlers) handler(editorX,editorY)
    handlerFunctions?.onLoop()
    requestAnimationFrame(updateLoop)
  }

  // handle editor view size changed event
  function updateEditorSize() {
    handlerFunctions?.onEditorSizeUpdated(editor.clientWidth,editor.clientHeight)
  }
  new ResizeObserver(updateEditorSize).observe(editor)
  updateEditorSize()

  // startup
  updateLoop()
  handlerFunctions?.onReady()
}

// Main handler code
let cursorCanvas,rainbowCursorHandle
createCursorHandler({

  // cursor create/destroy event handler polling rate
  cursorUpdatePollingRate: CursorUpdatePollingRate,

  // When editor instance stared
  onStarted: (editor)=>{
    // create new canvas for make animation
    cursorCanvas = document.createElement("canvas")
    cursorCanvas.style.pointerEvents = "none"
    cursorCanvas.style.position = "absolute"
    cursorCanvas.style.top = "0px"
    cursorCanvas.style.left = "0px"
    cursorCanvas.style.zIndex = "1000"
    editor.appendChild(cursorCanvas)

    // create rainbow cursor effect
    // thanks to https://github.com/tholman/cursor-effects/blob/master/src/rainbowCursor.js
    // we can create trail effect!
    let color = Color
    if (color == "default") {
      color = getComputedStyle(
        document.querySelector("body>.monaco-workbench"))
        .getPropertyValue("--vscode-editorCursor-background")
        .trim()
    }

    rainbowCursorHandle = createTrail({
      length: TrailLength,
      color: color,
      size: 7,
      style: CursorStyle,
      canvas: cursorCanvas
    })
  },

  onReady:()=>{},

  // when cursor moved
  onCursorPositionUpdated: (x,y)=>{
    rainbowCursorHandle.move(x,y)
  },

  // when editor view size changed
  onEditorSizeUpdated: (x,y)=>{
    rainbowCursorHandle.updateSize(x,y)
  },

  // when cursor size changed (emoji, ...)
  onCursorSizeUpdated: (x,y)=>{
    rainbowCursorHandle.updateCursorSize(x,y)
    // rainbowCursorHandle.updateCursorSize(parseInt(y/lineHeight))
  },

  // when using multi cursor... just hide all
  onCursorVisibilityChanged: (visibility)=>{
    cursorCanvas.style.visibility = visibility
  },

  // update animation
  onLoop: ()=>{
    rainbowCursorHandle.updateParticles()
  },

})
