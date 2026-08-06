const fs = require("fs");
const path = require("path");
const pptxgen = require("pptxgenjs");

const resultsPath = process.argv[2] || path.join(__dirname, "results", "benchmark_results.json");
const outputPath = process.argv[3] || path.join(__dirname, "results", "whisper_model_benchmark.pptx");
const inputName = process.argv[4] || "benchmark audio sample";
const referenceName = "whisper-large-v3-turbo-qnn";

if (!fs.existsSync(resultsPath)) {
  throw new Error(`Benchmark results not found: ${resultsPath}`);
}

const results = JSON.parse(fs.readFileSync(resultsPath, "utf8"));
const successful = results.filter((result) => result.status === "ok");
const failed = results.filter((result) => result.status !== "ok");
if (successful.length < 2) {
  throw new Error("At least two successful model results are required to build the comparison deck");
}

const reference = successful.find((result) => result.model === referenceName);
if (!reference) {
  throw new Error(`Reference result not found: ${referenceName}`);
}

const nonReference = successful.filter((result) => result.model !== referenceName);
const footprintResults = successful.filter(
  (result) => result.storage_mb != null && result.memory_loaded_mb != null
);
const fastest = [...successful].sort((a, b) => b.realtime_speed - a.realtime_speed)[0];
const mostAligned = [...nonReference].sort(
  (a, b) => a.wer - b.wer || a.cer - b.cer || b.realtime_speed - a.realtime_speed
)[0];
const longestModel = Math.max(...successful.map((result) => result.model.length));
const runDate = new Date().toLocaleDateString("en-US", {
  year: "numeric",
  month: "long",
  day: "numeric",
});

const COLORS = {
  ink: "14211F",
  pine: "174C45",
  green: "2D7A68",
  mint: "8ED1BE",
  lime: "C9E265",
  coral: "EF6A57",
  cream: "F6F3EA",
  paper: "FFFEFA",
  fog: "DDE5E1",
  muted: "62716D",
  white: "FFFFFF",
};

const pptx = new pptxgen();
pptx.layout = "LAYOUT_WIDE";
pptx.author = "Foundry Local Whisper";
pptx.company = "Microsoft";
pptx.subject = "Local Whisper model speed and transcription-alignment benchmark";
pptx.title = "Whisper Model Benchmark";
pptx.lang = "en-US";
pptx.theme = {
  headFontFace: "Aptos Display",
  bodyFontFace: "Aptos",
  lang: "en-US",
};

function addFooter(slide, number, dark = false) {
  slide.addText("FOUNDRY LOCAL WHISPER  /  MODEL BENCHMARK", {
    x: 0.55, y: 7.12, w: 5.4, h: 0.16, margin: 0,
    fontFace: "Aptos", fontSize: 8, bold: true, charSpacing: 1.4,
    color: dark ? COLORS.mint : COLORS.muted,
  });
  slide.addText(String(number).padStart(2, "0"), {
    x: 12.15, y: 7.08, w: 0.55, h: 0.2, margin: 0,
    fontFace: "Consolas", fontSize: 9, bold: true, align: "right",
    color: dark ? COLORS.white : COLORS.ink,
  });
}

function addTitle(slide, title, kicker) {
  slide.addText(kicker.toUpperCase(), {
    x: 0.62, y: 0.42, w: 4.8, h: 0.2, margin: 0,
    fontSize: 10, bold: true, charSpacing: 2.2, color: COLORS.green,
  });
  slide.addText(title, {
    x: 0.62, y: 0.74, w: 11.8, h: 0.55, margin: 0,
    fontFace: "Aptos Display", fontSize: 28, bold: true, color: COLORS.ink,
  });
}

function addMetric(slide, x, y, w, value, label, accent = COLORS.green) {
  slide.addShape(pptx.ShapeType.rect, {
    x, y, w, h: 1.28,
    fill: { color: COLORS.paper }, line: { color: COLORS.fog, width: 1 },
  });
  slide.addShape(pptx.ShapeType.rect, {
    x, y, w: 0.08, h: 1.28,
    fill: { color: accent }, line: { color: accent, transparency: 100 },
  });
  slide.addText(value, {
    x: x + 0.25, y: y + 0.18, w: w - 0.4, h: 0.48, margin: 0,
    fontFace: "Aptos Display", fontSize: 27, bold: true, color: COLORS.ink,
  });
  slide.addText(label, {
    x: x + 0.25, y: y + 0.8, w: w - 0.4, h: 0.2, margin: 0,
    fontSize: 10, bold: true, color: COLORS.muted, charSpacing: 0.7,
  });
}

function shortModel(model) {
  return model
    .replace("whisper-large-v3-turbo-qnn", "large-v3 turbo / QNN")
    .replace("whisper-", "");
}

function formatDuration(seconds) {
  const minutes = Math.floor(seconds / 60);
  const remainder = Math.round(seconds % 60);
  return `${minutes}m ${String(remainder).padStart(2, "0")}s`;
}

// Slide 1: title
{
  const slide = pptx.addSlide();
  slide.background = { color: COLORS.ink };
  slide.addShape(pptx.ShapeType.arc, {
    x: 8.25, y: -1.2, w: 6.0, h: 6.0, adjustPoint: 0.35,
    rotate: 18, fill: { color: COLORS.green, transparency: 12 },
    line: { color: COLORS.green, transparency: 100 },
  });
  [0, 1, 2, 3, 4, 5, 6].forEach((index) => {
    const height = 0.45 + (index % 4) * 0.32;
    slide.addShape(pptx.ShapeType.rect, {
      x: 9.15 + index * 0.38, y: 3.72 - height / 2, w: 0.14, h: height,
      fill: { color: index === 3 ? COLORS.lime : COLORS.mint },
      line: { color: COLORS.mint, transparency: 100 },
    });
  });
  slide.addText("LOCAL SPEECH AI / PERFORMANCE REVIEW", {
    x: 0.72, y: 0.68, w: 5.8, h: 0.25, margin: 0,
    fontSize: 11, bold: true, charSpacing: 2.3, color: COLORS.mint,
  });
  slide.addText("Whisper model\nbenchmark", {
    x: 0.72, y: 1.48, w: 7.4, h: 1.72, margin: 0,
    fontFace: "Aptos Display", fontSize: 45, bold: true, color: COLORS.white,
    breakLine: false,
  });
  slide.addText("Speed, throughput and transcript alignment on local hardware", {
    x: 0.75, y: 3.55, w: 6.5, h: 0.5, margin: 0,
    fontSize: 19, color: COLORS.fog,
  });
  slide.addText(`${runDate}  |  ${successful.length} models completed  |  ${formatDuration(reference.audio_s)} source audio`, {
    x: 0.75, y: 5.82, w: 8.0, h: 0.25, margin: 0,
    fontFace: "Consolas", fontSize: 10, color: COLORS.mint,
  });
  addFooter(slide, 1, true);
}

// Slide 2: methodology
{
  const slide = pptx.addSlide();
  slide.background = { color: COLORS.cream };
  addTitle(slide, "One recording. One pipeline. Comparable outputs.", "Benchmark design");
  const steps = [
    ["01", "INPUT", formatDuration(reference.audio_s), "Representative PCM WAV sample split near silence into <=30-second chunks"],
    ["02", "INFERENCE", `${successful.length} models`, "Each available model transcribed the same audio chunks"],
    ["03", "SCORING", "QNN reference", "WER and CER measure alignment to large-v3-turbo-QNN"],
  ];
  steps.forEach(([number, label, value, body], index) => {
    const x = 0.68 + index * 4.15;
    slide.addShape(pptx.ShapeType.rect, {
      x, y: 1.66, w: 3.62, h: 3.58,
      fill: { color: COLORS.paper }, line: { color: COLORS.fog, width: 1 },
    });
    slide.addText(number, {
      x: x + 0.25, y: 1.92, w: 0.75, h: 0.42, margin: 0,
      fontFace: "Consolas", fontSize: 22, bold: true, color: COLORS.green,
    });
    slide.addText(label, {
      x: x + 1.02, y: 2.02, w: 1.7, h: 0.2, margin: 0,
      fontSize: 10, bold: true, charSpacing: 1.5, color: COLORS.muted,
    });
    slide.addText(value, {
      x: x + 0.25, y: 2.67, w: 3.0, h: 0.55, margin: 0,
      fontFace: "Aptos Display", fontSize: 27, bold: true, color: COLORS.ink,
    });
    slide.addText(body, {
      x: x + 0.25, y: 3.58, w: 2.95, h: 0.72, margin: 0,
      fontSize: 14, color: COLORS.muted, breakLine: false,
    });
  });
  slide.addText("Interpretation guardrail", {
    x: 0.72, y: 5.73, w: 2.1, h: 0.24, margin: 0,
    fontSize: 12, bold: true, color: COLORS.coral,
  });
  slide.addText("Accuracy is relative to the QNN transcript, not a human-verified ground truth transcript.", {
    x: 2.52, y: 5.72, w: 8.9, h: 0.28, margin: 0,
    fontSize: 13, color: COLORS.ink,
  });
  slide.addText(`Source: ${inputName}`, {
    x: 0.72, y: 6.42, w: 7.2, h: 0.2, margin: 0,
    fontFace: "Consolas", fontSize: 9, color: COLORS.muted,
  });
  addFooter(slide, 2);
}

// Slide 3: headline results
{
  const slide = pptx.addSlide();
  slide.background = { color: COLORS.cream };
  addTitle(slide, "The benchmark at a glance", "Headline KPIs");
  addMetric(slide, 0.68, 1.58, 2.85, `${fastest.realtime_speed.toFixed(1)}x`, "FASTEST REAL-TIME SPEED", COLORS.lime);
  addMetric(slide, 3.72, 1.58, 2.85, `${mostAligned.word_accuracy_pct.toFixed(1)}%`, "BEST NON-REFERENCE ALIGNMENT", COLORS.green);
  addMetric(slide, 6.76, 1.58, 2.85, formatDuration(fastest.latency_s), "FASTEST END-TO-END LATENCY", COLORS.mint);
  addMetric(slide, 9.80, 1.58, 2.85, reference.word_count.toLocaleString("en-US"), "REFERENCE WORDS", COLORS.coral);

  slide.addShape(pptx.ShapeType.rect, {
    x: 0.68, y: 3.28, w: 11.97, h: 2.7,
    fill: { color: COLORS.pine }, line: { color: COLORS.pine },
  });
  slide.addText("Decision signal", {
    x: 1.05, y: 3.67, w: 2.1, h: 0.28, margin: 0,
    fontSize: 12, bold: true, charSpacing: 1.3, color: COLORS.lime,
  });
  slide.addText(`${shortModel(fastest.model)} leads throughput`, {
    x: 1.05, y: 4.08, w: 5.45, h: 0.48, margin: 0,
    fontFace: "Aptos Display", fontSize: 25, bold: true, color: COLORS.white,
  });
  slide.addText(`${shortModel(mostAligned.model)} is closest to the QNN reference`, {
    x: 6.74, y: 4.08, w: 5.25, h: 0.48, margin: 0,
    fontFace: "Aptos Display", fontSize: 22, bold: true, color: COLORS.white,
  });
  slide.addText(`${fastest.realtime_speed.toFixed(1)} hours of audio processed per wall-clock hour`, {
    x: 1.05, y: 4.88, w: 5.2, h: 0.26, margin: 0,
    fontSize: 13, color: COLORS.mint,
  });
  slide.addText(`WER ${mostAligned.wer.toFixed(3)}  |  CER ${mostAligned.cer.toFixed(3)}`, {
    x: 6.74, y: 4.88, w: 4.8, h: 0.26, margin: 0,
    fontFace: "Consolas", fontSize: 13, color: COLORS.mint,
  });
  addFooter(slide, 3);
}

// Slide 4: throughput
{
  const slide = pptx.addSlide();
  slide.background = { color: COLORS.paper };
  addTitle(slide, "Throughput separates the deployment options", "Speed comparison");
  const speedResults = [...successful].sort((a, b) => b.realtime_speed - a.realtime_speed);
  slide.addChart(pptx.ChartType.bar, [{
    name: "Real-time speed",
    labels: speedResults.map((result) => shortModel(result.model)),
    values: speedResults.map((result) => Number(result.realtime_speed.toFixed(2))),
  }], {
    x: 0.75, y: 1.55, w: 8.25, h: 4.75,
    barDir: "bar", catAxisLabelFontFace: "Aptos", catAxisLabelFontSize: longestModel > 22 ? 10 : 12,
    valAxisLabelFontFace: "Consolas", valAxisLabelFontSize: 9,
    valAxisMinVal: 0, showTitle: false, showLegend: false, showValue: true,
    dataLabelPosition: "outEnd", dataLabelColor: COLORS.ink, dataLabelFormatCode: "0.0x",
    chartColors: [COLORS.green],
    chartArea: { fill: { color: COLORS.paper }, line: { color: COLORS.paper } },
    plotArea: { fill: { color: COLORS.paper }, line: { color: COLORS.paper } },
    valGridLine: { color: COLORS.fog, width: 1 }, catGridLine: { style: "none" },
    showCatName: false, showValAxisTitle: true, valAxisTitle: "Real-time multiplier (higher is better)",
  });
  slide.addShape(pptx.ShapeType.rect, {
    x: 9.42, y: 1.72, w: 3.15, h: 3.72,
    fill: { color: COLORS.cream }, line: { color: COLORS.fog, width: 1 },
  });
  slide.addText("How to read it", {
    x: 9.73, y: 2.05, w: 2.5, h: 0.3, margin: 0,
    fontSize: 15, bold: true, color: COLORS.ink,
  });
  slide.addText([
    { text: "1.0x", options: { bold: true, color: COLORS.green } },
    { text: " means real-time processing.", options: { breakLine: true } },
    { text: `${fastest.realtime_speed.toFixed(1)}x`, options: { bold: true, color: COLORS.green } },
    { text: ` means one hour of audio in about ${Math.round(60 / fastest.realtime_speed)} minutes.`, options: { breakLine: true } },
    { text: "Latency includes transcription of all chunks, not model loading." },
  ], {
    x: 9.73, y: 2.62, w: 2.35, h: 1.78, margin: 0,
    fontSize: 13, color: COLORS.muted, breakLine: false, paraSpaceAfterPt: 13,
  });
  addFooter(slide, 4);
}

// Slide 5: transcript alignment
{
  const slide = pptx.addSlide();
  slide.background = { color: COLORS.cream };
  addTitle(slide, "Transcript alignment reveals the quality trade-off", "Relative accuracy");
  const alignmentResults = [...successful].sort((a, b) => b.word_accuracy_pct - a.word_accuracy_pct);
  slide.addChart(pptx.ChartType.bar, [{
    name: "Word accuracy",
    labels: alignmentResults.map((result) => shortModel(result.model)),
    values: alignmentResults.map((result) => Number(result.word_accuracy_pct.toFixed(1))),
  }], {
    x: 0.75, y: 1.55, w: 7.8, h: 4.7,
    barDir: "bar", showTitle: false, showLegend: false, showValue: true,
    dataLabelPosition: "outEnd", dataLabelColor: COLORS.ink, dataLabelFormatCode: "0.0",
    chartColors: [COLORS.coral],
    catAxisLabelFontFace: "Aptos", catAxisLabelFontSize: 11,
    valAxisLabelFontFace: "Consolas", valAxisLabelFontSize: 9,
    valAxisMinVal: 0, valAxisMaxVal: 100, valAxisMajorUnit: 20,
    chartArea: { fill: { color: COLORS.cream }, line: { color: COLORS.cream } },
    plotArea: { fill: { color: COLORS.cream }, line: { color: COLORS.cream } },
    valGridLine: { color: COLORS.fog, width: 1 }, catGridLine: { style: "none" },
    showValAxisTitle: true, valAxisTitle: "Word accuracy vs. QNN reference",
  });
  slide.addShape(pptx.ShapeType.rect, {
    x: 8.95, y: 1.68, w: 3.65, h: 4.25,
    fill: { color: COLORS.ink }, line: { color: COLORS.ink },
  });
  slide.addText("Closest non-reference", {
    x: 9.3, y: 2.02, w: 2.9, h: 0.25, margin: 0,
    fontSize: 11, bold: true, charSpacing: 1.1, color: COLORS.mint,
  });
  slide.addText(shortModel(mostAligned.model), {
    x: 9.3, y: 2.52, w: 2.85, h: 0.68, margin: 0,
    fontFace: "Aptos Display", fontSize: 25, bold: true, color: COLORS.white,
  });
  slide.addText(`${mostAligned.word_accuracy_pct.toFixed(1)}%`, {
    x: 9.3, y: 3.48, w: 2.85, h: 0.68, margin: 0,
    fontFace: "Consolas", fontSize: 32, bold: true, color: COLORS.lime,
  });
  slide.addText(`Substitutions ${mostAligned.word_substitutions.toLocaleString()}\nDeletions ${mostAligned.word_deletions.toLocaleString()}\nInsertions ${mostAligned.word_insertions.toLocaleString()}`, {
    x: 9.3, y: 4.43, w: 2.85, h: 0.9, margin: 0,
    fontFace: "Consolas", fontSize: 12, color: COLORS.fog, breakLine: false,
  });
  slide.addText("Reference model scores 100% by definition.", {
    x: 0.78, y: 6.46, w: 5.8, h: 0.2, margin: 0,
    fontSize: 10, italic: true, color: COLORS.muted,
  });
  addFooter(slide, 5);
}

// Slide 6: storage and memory footprint
{
  const slide = pptx.addSlide();
  slide.background = { color: COLORS.paper };
  addTitle(slide, "Footprint scales sharply with model capacity", "Storage and host memory");
  const sortedFootprints = [...footprintResults].sort((a, b) => a.storage_mb - b.storage_mb);
  slide.addChart(pptx.ChartType.bar, [
    {
      name: "Storage",
      labels: sortedFootprints.map((result) => shortModel(result.model)),
      values: sortedFootprints.map((result) => Math.round(result.storage_mb)),
    },
    {
      name: "Loaded host memory",
      labels: sortedFootprints.map((result) => shortModel(result.model)),
      values: sortedFootprints.map((result) => Math.round(result.memory_loaded_mb)),
    },
  ], {
    x: 0.75, y: 1.48, w: 8.45, h: 4.95,
    barDir: "bar", grouping: "clustered", showTitle: false, showLegend: true,
    legendPos: "b", legendFontFace: "Aptos", legendFontSize: 10,
    showValue: true, dataLabelPosition: "outEnd", dataLabelColor: COLORS.ink,
    dataLabelFormatCode: "0\" MB\"", chartColors: [COLORS.green, COLORS.coral],
    catAxisLabelFontFace: "Aptos", catAxisLabelFontSize: 10,
    valAxisLabelFontFace: "Consolas", valAxisLabelFontSize: 9,
    valAxisMinVal: 0, valGridLine: { color: COLORS.fog, width: 1 },
    catGridLine: { style: "none" },
    chartArea: { fill: { color: COLORS.paper }, line: { color: COLORS.paper } },
    plotArea: { fill: { color: COLORS.paper }, line: { color: COLORS.paper } },
    showValAxisTitle: true, valAxisTitle: "Megabytes (lower is better)",
  });
  slide.addShape(pptx.ShapeType.rect, {
    x: 9.55, y: 1.7, w: 3.0, h: 4.15,
    fill: { color: COLORS.cream }, line: { color: COLORS.fog, width: 1 },
  });
  slide.addText("Smallest in both", {
    x: 9.86, y: 2.03, w: 2.35, h: 0.24, margin: 0,
    fontSize: 11, bold: true, charSpacing: 1.0, color: COLORS.green,
  });
  const smallest = sortedFootprints[0];
  slide.addText(shortModel(smallest.model), {
    x: 9.86, y: 2.47, w: 2.25, h: 0.48, margin: 0,
    fontFace: "Aptos Display", fontSize: 26, bold: true, color: COLORS.ink,
  });
  slide.addText(`${Math.round(smallest.storage_mb)} MB`, {
    x: 9.86, y: 3.18, w: 2.25, h: 0.46, margin: 0,
    fontFace: "Consolas", fontSize: 24, bold: true, color: COLORS.green,
  });
  slide.addText("model package", {
    x: 9.86, y: 3.68, w: 2.25, h: 0.2, margin: 0,
    fontSize: 11, color: COLORS.muted,
  });
  slide.addText(`${Math.round(smallest.memory_loaded_mb)} MB`, {
    x: 9.86, y: 4.22, w: 2.25, h: 0.46, margin: 0,
    fontFace: "Consolas", fontSize: 24, bold: true, color: COLORS.coral,
  });
  slide.addText("loaded host working set", {
    x: 9.86, y: 4.72, w: 2.25, h: 0.38, margin: 0,
    fontSize: 11, color: COLORS.muted,
  });
  slide.addShape(pptx.ShapeType.rect, {
    x: 0.75, y: 6.46, w: 11.82, h: 0.48,
    fill: { color: COLORS.cream }, line: { color: COLORS.fog, width: 1 },
  });
  slide.addText(
    "Measurement note: isolated Python working set after model load; accelerator-reserved GPU/NPU memory may not be attributed to the host process.",
    {
      x: 0.98, y: 6.59, w: 11.2, h: 0.2, margin: 0,
      fontSize: 10, color: COLORS.ink,
    }
  );
  addFooter(slide, 6);
}

// Slide 7: model matrix and recommendation
{
  const slide = pptx.addSlide();
  slide.background = { color: COLORS.ink };
  slide.addText("Recommendation", {
    x: 0.72, y: 0.56, w: 5.3, h: 0.55, margin: 0,
    fontFace: "Aptos Display", fontSize: 31, bold: true, color: COLORS.white,
  });
  slide.addText("Choose by workload, then validate against human ground truth", {
    x: 0.72, y: 1.25, w: 8.8, h: 0.35, margin: 0,
    fontSize: 16, color: COLORS.mint,
  });
  slide.addText("WORKLOAD WINNERS", {
    x: 10.38, y: 0.7, w: 2.2, h: 0.2, margin: 0,
    fontSize: 9, bold: true, charSpacing: 1.4, align: "right", color: COLORS.lime,
  });

  const cards = [
    {
      x: 0.72, title: "Interactive dictation", model: shortModel(fastest.model),
      accent: COLORS.lime,
      body: `Prioritize ${fastest.realtime_speed.toFixed(1)}x throughput and low end-to-end latency.`,
    },
    {
      x: 4.83, title: "Quality-sensitive drafts", model: shortModel(mostAligned.model),
      accent: COLORS.mint,
      body: `Best observed non-reference alignment at ${mostAligned.word_accuracy_pct.toFixed(1)}%.`,
    },
    {
      x: 8.94, title: "NPU reference path", model: "large-v3 turbo / QNN",
      accent: COLORS.coral,
      body: "Keep as the local comparison anchor; validate absolute accuracy separately.",
    },
  ];
  cards.forEach((card) => {
    slide.addShape(pptx.ShapeType.rect, {
      x: card.x, y: 2.04, w: 3.55, h: 3.42,
      fill: { color: COLORS.pine }, line: { color: COLORS.green, width: 1 },
    });
    slide.addShape(pptx.ShapeType.rect, {
      x: card.x, y: 2.04, w: 3.55, h: 0.09,
      fill: { color: card.accent }, line: { color: card.accent },
    });
    slide.addText(card.title.toUpperCase(), {
      x: card.x + 0.28, y: 2.42, w: 2.9, h: 0.22, margin: 0,
      fontSize: 10, bold: true, charSpacing: 1.2, color: card.accent,
    });
    slide.addText(card.model, {
      x: card.x + 0.28, y: 2.98, w: 2.95, h: 0.72, margin: 0,
      fontFace: "Aptos Display", fontSize: 22, bold: true, color: COLORS.white,
    });
    slide.addText(card.body, {
      x: card.x + 0.28, y: 4.08, w: 2.9, h: 0.72, margin: 0,
      fontSize: 13, color: COLORS.fog,
    });
  });
  const failureText = failed.length
    ? `Run note: unavailable catalog entry ${failed.map((result) => result.model).join(", ")} is excluded; base and medium completed but did not lead a featured workload KPI.`
    : "Run note: all discovered models completed successfully.";
  slide.addText(failureText, {
    x: 0.75, y: 6.18, w: 11.5, h: 0.42, margin: 0,
    fontSize: 11, color: COLORS.mint,
  });
  addFooter(slide, 7, true);
}

pptx.writeFile({ fileName: outputPath });