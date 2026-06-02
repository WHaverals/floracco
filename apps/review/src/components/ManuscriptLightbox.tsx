import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

type Point = { x: number; y: number };
type Size = { w: number; h: number };
type View = { scale: number; translate: Point; fitScale: number };

const MIN_SCALE = 0.05;
const MAX_SCALE = 8;
const WHEEL_SENSITIVITY = 0.0025;
const DOUBLE_CLICK_SCALE = 2.5;

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function zoomAtPoint(
  scale: number,
  translate: Point,
  pointer: Point,
  nextScale: number,
): Pick<View, "scale" | "translate"> {
  const imageX = (pointer.x - translate.x) / scale;
  const imageY = (pointer.y - translate.y) / scale;
  return {
    scale: nextScale,
    translate: {
      x: pointer.x - imageX * nextScale,
      y: pointer.y - imageY * nextScale,
    },
  };
}

function centerTranslate(container: Size, image: Size, scale: number): Point {
  return {
    x: (container.w - image.w * scale) / 2,
    y: (container.h - image.h * scale) / 2,
  };
}

function fitScaleFor(container: Size, image: Size): number {
  if (!image.w || !image.h || !container.w || !container.h) {
    return 1;
  }
  return Math.min(container.w / image.w, container.h / image.h);
}

function normalizeWheelDelta(event: WheelEvent): number {
  let delta = event.deltaY;
  if (event.deltaMode === WheelEvent.DOM_DELTA_LINE) {
    delta *= 16;
  } else if (event.deltaMode === WheelEvent.DOM_DELTA_PAGE) {
    delta *= 400;
  }
  return delta;
}

type Props = {
  src: string;
  alt: string;
  onClose: () => void;
  label?: string;
  toolbarExtra?: ReactNode;
  zIndex?: number;
};

export default function ManuscriptLightbox({ src, alt, onClose, label, toolbarExtra, zIndex = 200 }: Props) {
  const stageRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const naturalRef = useRef<Size>({ w: 0, h: 0 });
  const containerRef = useRef<Size>({ w: 0, h: 0 });
  const viewRef = useRef<View>({ scale: 1, translate: { x: 0, y: 0 }, fitScale: 1 });
  const initializedForSrc = useRef<string | null>(null);
  const dragRef = useRef<{ active: boolean; startX: number; startY: number; originX: number; originY: number } | null>(
    null,
  );

  const [natural, setNatural] = useState<Size>({ w: 0, h: 0 });
  const [displayPercent, setDisplayPercent] = useState(100);
  const [ready, setReady] = useState(false);

  const paintTransform = useCallback((view: Partial<View>) => {
    viewRef.current = { ...viewRef.current, ...view };
    const node = contentRef.current;
    if (node) {
      node.style.transform = `translate(${viewRef.current.translate.x}px, ${viewRef.current.translate.y}px) scale(${viewRef.current.scale})`;
    }
    setDisplayPercent(Math.round(viewRef.current.scale * 100));
  }, []);

  const measureContainer = useCallback(() => {
    const stage = stageRef.current;
    if (!stage) {
      return;
    }
    containerRef.current = { w: stage.clientWidth, h: stage.clientHeight };
  }, []);

  const applyFit = useCallback(() => {
    const image = naturalRef.current;
    const box = containerRef.current;
    if (!image.w || !image.h || !box.w || !box.h) {
      return;
    }
    const fit = fitScaleFor(box, image);
    const next: View = {
      scale: fit,
      fitScale: fit,
      translate: centerTranslate(box, image, fit),
    };
    paintTransform(next);
  }, [paintTransform]);

  const applyOneToOne = useCallback(() => {
    const image = naturalRef.current;
    const box = containerRef.current;
    if (!image.w || !box.w) {
      return;
    }
    const fit = fitScaleFor(box, image);
    paintTransform({
      scale: 1,
      fitScale: fit,
      translate: centerTranslate(box, image, 1),
    });
  }, [paintTransform]);

  const setPresetScale = useCallback(
    (targetScale: number) => {
      const image = naturalRef.current;
      const box = containerRef.current;
      if (!image.w || !box.w) {
        return;
      }
      const fit = fitScaleFor(box, image);
      const next = clamp(targetScale, MIN_SCALE, MAX_SCALE);
      paintTransform({
        scale: next,
        fitScale: fit,
        translate: centerTranslate(box, image, next),
      });
    },
    [paintTransform],
  );

  const isNearFit = useCallback(() => {
    const image = naturalRef.current;
    const box = containerRef.current;
    const { scale, translate, fitScale } = viewRef.current;
    if (!image.w || !box.w) {
      return true;
    }
    const centered = centerTranslate(box, image, fitScale);
    return (
      Math.abs(scale - fitScale) < 0.001 &&
      Math.abs(translate.x - centered.x) < 2 &&
      Math.abs(translate.y - centered.y) < 2
    );
  }, []);

  const pointerInStage = useCallback((clientX: number, clientY: number): Point | null => {
    const stage = stageRef.current;
    if (!stage) {
      return null;
    }
    const rect = stage.getBoundingClientRect();
    return { x: clientX - rect.left, y: clientY - rect.top };
  }, []);

  const zoomAtPointer = useCallback(
    (factor: number, clientX: number, clientY: number) => {
      const pointer = pointerInStage(clientX, clientY);
      if (!pointer) {
        return;
      }
      const { scale, translate } = viewRef.current;
      const nextScale = clamp(scale * factor, MIN_SCALE, MAX_SCALE);
      if (Math.abs(nextScale - scale) < 0.0001) {
        return;
      }
      paintTransform(zoomAtPoint(scale, translate, pointer, nextScale));
    },
    [paintTransform, pointerInStage],
  );

  const zoomByCenter = useCallback(
    (factor: number) => {
      const stage = stageRef.current;
      if (!stage) {
        return;
      }
      const rect = stage.getBoundingClientRect();
      zoomAtPointer(factor, rect.left + rect.width / 2, rect.top + rect.height / 2);
    },
    [zoomAtPointer],
  );

  useEffect(() => {
    naturalRef.current = { w: 0, h: 0 };
    initializedForSrc.current = null;
    setNatural({ w: 0, h: 0 });
    setReady(false);
  }, [src]);

  useEffect(() => {
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prevOverflow;
    };
  }, []);

  useEffect(() => {
    measureContainer();
    const stage = stageRef.current;
    if (!stage) {
      return;
    }
    const observer = new ResizeObserver(measureContainer);
    observer.observe(stage);
    return () => observer.disconnect();
  }, [measureContainer]);

  useEffect(() => {
    if (!natural.w || !natural.h || !containerRef.current.w || !containerRef.current.h) {
      return;
    }
    if (initializedForSrc.current === src) {
      return;
    }
    initializedForSrc.current = src;
    applyFit();
    setReady(true);
  }, [src, natural, applyFit]);

  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) {
      return;
    }
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      event.stopPropagation();
      const delta = normalizeWheelDelta(event);
      const factor = Math.exp(-delta * WHEEL_SENSITIVITY);
      zoomAtPointer(factor, event.clientX, event.clientY);
    };
    stage.addEventListener("wheel", onWheel, { passive: false });
    return () => stage.removeEventListener("wheel", onWheel);
  }, [zoomAtPointer]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") {
        return;
      }
      event.stopPropagation();
      if (!naturalRef.current.w) {
        onClose();
        return;
      }
      if (isNearFit()) {
        onClose();
      } else {
        applyFit();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [applyFit, isNearFit, onClose]);

  const onPointerDown = (event: React.PointerEvent) => {
    if (event.button !== 0) {
      return;
    }
    event.currentTarget.setPointerCapture(event.pointerId);
    dragRef.current = {
      active: true,
      startX: event.clientX,
      startY: event.clientY,
      originX: viewRef.current.translate.x,
      originY: viewRef.current.translate.y,
    };
  };

  const onPointerMove = (event: React.PointerEvent) => {
    const drag = dragRef.current;
    if (!drag?.active) {
      return;
    }
    paintTransform({
      ...viewRef.current,
      translate: {
        x: drag.originX + (event.clientX - drag.startX),
        y: drag.originY + (event.clientY - drag.startY),
      },
    });
  };

  const onPointerUp = (event: React.PointerEvent) => {
    if (dragRef.current?.active) {
      dragRef.current.active = false;
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  };

  const onDoubleClick = (event: React.MouseEvent) => {
    event.preventDefault();
    const pointer = pointerInStage(event.clientX, event.clientY);
    if (!pointer || !naturalRef.current.w) {
      return;
    }
    if (isNearFit()) {
      const { scale, translate } = viewRef.current;
      const nextScale = clamp(scale * DOUBLE_CLICK_SCALE, MIN_SCALE, MAX_SCALE);
      paintTransform(zoomAtPoint(scale, translate, pointer, nextScale));
    } else {
      applyFit();
    }
  };

  const stopBubble = (event: React.MouseEvent) => {
    event.stopPropagation();
  };

  const ui = (
    <div
      className="manuscript-lightbox"
      style={{ zIndex }}
      role="dialog"
      aria-modal="true"
      aria-label="Manuscript viewer"
      onClick={stopBubble}
      onMouseDown={stopBubble}
    >
      <div className="manuscript-lightbox-toolbar" onClick={stopBubble}>
        <div className="manuscript-lightbox-toolbar-left">
          {label ? <span className="manuscript-lightbox-label">{label}</span> : null}
          <span className="manuscript-lightbox-zoom-readout">{displayPercent}%</span>
          <button
            type="button"
            className="manuscript-lightbox-btn"
            onClick={() => zoomByCenter(1 / 1.25)}
            aria-label="Zoom out"
          >
            −
          </button>
          <button
            type="button"
            className="manuscript-lightbox-btn"
            onClick={() => zoomByCenter(1.25)}
            aria-label="Zoom in"
          >
            +
          </button>
          <button type="button" className="manuscript-lightbox-btn" onClick={() => setPresetScale(1)}>
            100%
          </button>
          <button type="button" className="manuscript-lightbox-btn" onClick={() => setPresetScale(2)}>
            200%
          </button>
          <button type="button" className="manuscript-lightbox-btn" onClick={applyFit}>
            Fit
          </button>
          <button type="button" className="manuscript-lightbox-btn" title="Actual pixels" onClick={applyOneToOne}>
            1:1
          </button>
          <button type="button" className="manuscript-lightbox-btn" onClick={applyFit}>
            Reset
          </button>
        </div>
        <div className="manuscript-lightbox-toolbar-right">
          {toolbarExtra}
          <button type="button" className="manuscript-lightbox-btn manuscript-lightbox-close" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
      <p className="manuscript-lightbox-hint muted">
        Scroll to zoom at cursor · drag to pan · double-click to zoom in · Esc resets or closes
      </p>
      <div
        ref={stageRef}
        className="manuscript-lightbox-stage"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onDoubleClick={onDoubleClick}
      >
        <div
          ref={contentRef}
          className="manuscript-lightbox-content"
          style={{
            width: natural.w || undefined,
            height: natural.h || undefined,
          }}
        >
          <img
            src={src}
            alt={alt}
            draggable={false}
            width={natural.w || undefined}
            height={natural.h || undefined}
            onLoad={(event) => {
              const img = event.currentTarget;
              const size = { w: img.naturalWidth, h: img.naturalHeight };
              naturalRef.current = size;
              setNatural(size);
              measureContainer();
            }}
          />
        </div>
        {!ready && <p className="manuscript-lightbox-loading muted">Loading image…</p>}
      </div>
    </div>
  );

  return createPortal(ui, document.body);
}
