/* Examify browser OCR preprocessing worker.
 *
 * This worker keeps every cv.Mat and canvas scoped to one request. It uses the
 * self-hosted OpenCV build for deskew when available and a deterministic
 * projection fallback when a browser rejects OpenCV WASM. OCR itself is always
 * performed by Tesseract.js in a separate worker.
 */
let cvReadyPromise;
let cvUnavailable = false;

function loadOpenCv() {
  if (cvUnavailable) return Promise.resolve(null);
  if (cvReadyPromise) return cvReadyPromise;
  cvReadyPromise = new Promise((resolve) => {
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeoutId);
      if (!value) cvUnavailable = true;
      resolve(value || null);
    };
    // Deskew is an optional recovery optimization. A broken/missing OpenCV
    // asset must not hold the whole OCR pipeline for the old 8-second timeout;
    // projection thresholding below remains deterministic and safe.
    const timeoutId = setTimeout(() => finish(null), 1200);
    try {
      self.Module = {
        locateFile(path) {
          return path === "opencv_js.wasm" ? "/ocr/opencv/opencv_js.wasm" : `/ocr/opencv/${path}`;
        },
        onRuntimeInitialized() {
          finish(self.cv || null);
        },
      };
      importScripts("/ocr/opencv/opencv.js");
      if (self.cv && typeof self.cv.then === "function") {
        self.cv.then((value) => finish(value)).catch(() => finish(null));
      } else if (self.cv && self.cv.Mat) {
        finish(self.cv);
      }
    } catch {
      finish(null);
    }
  });
  return cvReadyPromise;
}

function luminance(data, offset) {
  return Math.round(data[offset] * 0.299 + data[offset + 1] * 0.587 + data[offset + 2] * 0.114);
}

function otsuThreshold(histogram, total) {
  let sum = 0;
  for (let value = 0; value < 256; value += 1) sum += value * histogram[value];
  let backgroundWeight = 0;
  let backgroundSum = 0;
  let maximum = -1;
  let threshold = 180;
  for (let value = 0; value < 256; value += 1) {
    backgroundWeight += histogram[value];
    if (!backgroundWeight) continue;
    const foregroundWeight = total - backgroundWeight;
    if (!foregroundWeight) break;
    backgroundSum += value * histogram[value];
    const backgroundMean = backgroundSum / backgroundWeight;
    const foregroundMean = (sum - backgroundSum) / foregroundWeight;
    const between = backgroundWeight * foregroundWeight * (backgroundMean - foregroundMean) ** 2;
    if (between > maximum) {
      maximum = between;
      threshold = value;
    }
  }
  return threshold;
}

function protectedWatermarkCleanup(image, boxes) {
  if (!boxes || !boxes.length) return image;
  const { data, width, height } = image;
  const gray = new Uint8Array(width * height);
  const histogram = new Uint32Array(256);
  for (let pixel = 0; pixel < gray.length; pixel += 1) {
    const value = luminance(data, pixel * 4);
    gray[pixel] = value;
    histogram[value] += 1;
  }
  const threshold = otsuThreshold(histogram, gray.length);
  for (const box of boxes) {
    const x0 = Math.max(1, Math.floor(box[0] * width));
    const y0 = Math.max(1, Math.floor(box[1] * height));
    const x1 = Math.min(width - 1, Math.ceil(box[2] * width));
    const y1 = Math.min(height - 1, Math.ceil(box[3] * height));
    for (let y = y0; y < y1; y += 1) {
      for (let x = x0; x < x1; x += 1) {
        const pixel = y * width + x;
        const value = gray[pixel];
        const gradient = Math.max(
          Math.abs(value - gray[pixel - 1]),
          Math.abs(value - gray[pixel + 1]),
          Math.abs(value - gray[pixel - width]),
          Math.abs(value - gray[pixel + width]),
        );
        // Only remove a bright, low-contrast background component. Dark text
        // and strong glyph edges are immutable evidence.
        if (value > Math.max(145, threshold - 8) && gradient < 28) {
          const offset = pixel * 4;
          data[offset] = 255;
          data[offset + 1] = 255;
          data[offset + 2] = 255;
        }
      }
    }
  }
  return image;
}

function grayscale(image) {
  const data = image.data;
  for (let offset = 0; offset < data.length; offset += 4) {
    const value = luminance(data, offset);
    data[offset] = value;
    data[offset + 1] = value;
    data[offset + 2] = value;
  }
  return image;
}

function localThreshold(image) {
  const { data, width, height } = image;
  const histogram = new Uint32Array(256);
  for (let offset = 0; offset < data.length; offset += 4) histogram[data[offset]] += 1;
  const threshold = Math.min(215, Math.max(120, otsuThreshold(histogram, width * height)));
  for (let offset = 0; offset < data.length; offset += 4) {
    const value = data[offset] < threshold ? 0 : 255;
    data[offset] = value;
    data[offset + 1] = value;
    data[offset + 2] = value;
  }
  return image;
}

async function openCvDeskew(image) {
  const cv = await loadOpenCv();
  if (!cv) return { image, angle: 0, engine: "projection" };
  let source;
  let gray;
  let edges;
  let lines;
  let rotation;
  let target;
  try {
    source = cv.matFromImageData(image);
    gray = new cv.Mat();
    edges = new cv.Mat();
    lines = new cv.Mat();
    cv.cvtColor(source, gray, cv.COLOR_RGBA2GRAY);
    cv.Canny(gray, edges, 50, 150, 3, false);
    cv.HoughLinesP(edges, lines, 1, Math.PI / 180, 80, Math.max(30, image.width / 12), 12);
    const angles = [];
    for (let row = 0; row < lines.rows; row += 1) {
      const x0 = lines.data32S[row * 4];
      const y0 = lines.data32S[row * 4 + 1];
      const x1 = lines.data32S[row * 4 + 2];
      const y1 = lines.data32S[row * 4 + 3];
      const angle = Math.atan2(y1 - y0, x1 - x0) * 180 / Math.PI;
      if (Math.abs(angle) <= 5) angles.push(angle);
    }
    if (!angles.length) return { image, angle: 0, engine: "opencv" };
    angles.sort((left, right) => left - right);
    const angle = angles[Math.floor(angles.length / 2)];
    if (Math.abs(angle) < 0.08) return { image, angle: 0, engine: "opencv" };
    const center = new cv.Point(source.cols / 2, source.rows / 2);
    rotation = cv.getRotationMatrix2D(center, angle, 1);
    target = new cv.Mat();
    cv.warpAffine(
      source,
      target,
      rotation,
      new cv.Size(source.cols, source.rows),
      cv.INTER_LINEAR,
      cv.BORDER_CONSTANT,
      new cv.Scalar(255, 255, 255, 255),
    );
    return {
      image: new ImageData(new Uint8ClampedArray(target.data), target.cols, target.rows),
      angle,
      engine: "opencv",
    };
  } catch {
    return { image, angle: 0, engine: "projection" };
  } finally {
    if (source) source.delete();
    if (gray) gray.delete();
    if (edges) edges.delete();
    if (lines) lines.delete();
    if (rotation) rotation.delete();
    if (target) target.delete();
  }
}

self.onmessage = async (event) => {
  const { id, image, mode, watermarkBoxes } = event.data;
  try {
    let result = grayscale(new ImageData(new Uint8ClampedArray(image.data), image.width, image.height));
    let deskew = { image: result, angle: 0, engine: "none" };
    if (mode === "recovery") {
      result = protectedWatermarkCleanup(result, watermarkBoxes || []);
      deskew = await openCvDeskew(result);
      result = localThreshold(deskew.image);
    }
    self.postMessage(
      { id, ok: true, image: result, angle: deskew.angle, engine: deskew.engine },
      [result.data.buffer],
    );
  } catch (error) {
    self.postMessage({ id, ok: false, error: error instanceof Error ? error.message : String(error) });
  }
};
