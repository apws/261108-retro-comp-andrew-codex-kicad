// Load after rendering in Asciidoctor.js Live Preview.
// Tag modifier labels; all appearance stays in the shared stylesheet.
// This also works when the helper is included in exported HTML.

// Use the source filename stem as the tab/print title. Chromium's Save as
// PDF can then suggest the same name with .pdf. Keep the visible heading.
// This must run AFTER the preview extension sets document.title. Run it
// again before printing in case another renderer has subsequently set it.
;(() => {
  function useSourceFilenameAsTitle() {
    const encodedName = new URL(location.href).pathname.split('/').pop()
    let name
    try {
      name = decodeURIComponent(encodedName)
    } catch {
      return
    }
    if (/\.(?:adoc|asciidoc|asc|ad)$/i.test(name)) {
      document.title = name.replace(/\.(?:adoc|asciidoc|asc|ad)$/i, '')
    }
  }

  useSourceFilenameAsTitle()
  if (!window.__mixworxFilenamePrintTitleInstalled) {
    window.addEventListener('beforeprint', useSourceFilenameAsTitle)
    window.__mixworxFilenamePrintTitleInstalled = true
  }
})()

for (const key of document.querySelectorAll('kbd')) {
  key.classList.toggle('modifier', /^(ctrl|alt|altgr|shift|fn)$/i.test(key.textContent.trim()))
}

// Keep source headings portable. Split the first prose ": " at any rendered
// heading level, independent of the document's :leveloffset: setting.
if (location.pathname.endsWith('.adoc') ||
    document.querySelector('meta[name="generator"][content^="Asciidoctor"]')) {
  const headings = document.querySelectorAll(
    '#header :is(h1,h2,h3,h4,h5,h6), #content :is(h1,h2,h3,h4,h5,h6)'
  )
  for (const heading of headings) {
    if (heading.dataset.mixworxColonBreak || heading.querySelector('br')) continue

    const walker = document.createTreeWalker(heading, NodeFilter.SHOW_TEXT)
    const nodes = []
    let text = ''
    let node
    while ((node = walker.nextNode())) {
      nodes.push({ node, start: text.length })
      text += node.data
    }
    for (const separator of text.matchAll(/:\s+(?=\S)/g)) {
      const start = nodes.findLast(entry => entry.start <= separator.index)
      if (start.node.parentElement.closest('code, kbd, samp, .anchor')) continue
      const endOffset = separator.index + separator[0].length
      const end = nodes.findLast(entry => entry.start < endOffset)

      // A Range changes only the separator, retaining inline emphasis, links,
      // heading IDs and anchor controls, even across inline markup boundaries.
      const range = document.createRange()
      range.setStart(start.node, separator.index - start.start)
      range.setEnd(end.node, endOffset - end.start)
      range.deleteContents()
      range.insertNode(document.createElement('br'))
      heading.dataset.mixworxColonBreak = 'true'
      break
    }
  }

  // Restore fragment position after rendering AND all heading-height changes.
  if (location.hash.length > 1) {
    const id = decodeURIComponent(location.hash.slice(1))
    document.getElementById(id)?.scrollIntoView({ block: 'start' })
  }
}
