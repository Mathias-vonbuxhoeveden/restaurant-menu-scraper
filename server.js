"use strict";

const express = require("express");
const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");
const https = require("https");
const http = require("http");

const app = express();
app.use(express.json());

const PORT = process.env.PORT || 3000;
const CALLBACK_URL =
  "https://sfxwhxvspdultgtvjphj.supabase.co/functions/v1/ingest-menu";

// ---------------------------------------------------------------------------
// Health check
// ---------------------------------------------------------------------------

app.get("/health", (_req, res) => {
  res.json({ status: "ok", timestamp: new Date().toISOString() });
});

// ---------------------------------------------------------------------------
// POST /api/scrape
// ---------------------------------------------------------------------------

app.post("/api/scrape", (req, res) => {
  const { prospect_id, slug, restaurant_name, address, email, competitors } =
    req.body || {};

  if (!restaurant_name || !address || !competitors) {
    return res.status(400).json({
      error: "Missing required fields: restaurant_name, address, competitors",
    });
  }

  // Respond immediately
  res.status(202).json({
    message: "Scraping started",
    prospect_id,
    slug,
  });

  // Fire-and-forget — errors are logged, never thrown back to the caller
  processRequest({
    prospect_id,
    slug,
    restaurant_name,
    address,
    email,
    competitors,
  }).catch((err) =>
    console.error(`[${slug || prospect_id}] Unhandled error:`, err.message)
  );
});

// ---------------------------------------------------------------------------
// Async processing
// ---------------------------------------------------------------------------

async function processRequest({
  prospect_id,
  slug,
  restaurant_name,
  address,
  competitors,
}) {
  const jobId = `${slug || prospect_id || "job"}-${Date.now()}`;
  const outputPath = path.join("/tmp", `${jobId}.xlsx`);

  console.log(`[${jobId}] Starting scrape for: ${restaurant_name}`);
  console.log(`[${jobId}] Address: ${address}`);
  console.log(`[${jobId}] Competitors: ${competitors}`);

  try {
    await runPipeline({ restaurant_name, address, competitors, outputPath });

    if (!fs.existsSync(outputPath)) {
      throw new Error(`Output file not found at ${outputPath}`);
    }

    console.log(`[${jobId}] Scraping complete — encoding Excel file`);
    const fileBuffer = fs.readFileSync(outputPath);
    const fileBase64 = fileBuffer.toString("base64");
    const filename = `${slug || prospect_id || restaurant_name}.xlsx`;

    console.log(`[${jobId}] Sending callback to Supabase (${filename})`);
    await sendCallback({ prospect_id, slug, fileBase64, filename });

    console.log(`[${jobId}] Done`);
  } catch (err) {
    console.error(`[${jobId}] Error: ${err.message}`);
  } finally {
    try {
      if (fs.existsSync(outputPath)) fs.unlinkSync(outputPath);
    } catch (_) {
      // ignore cleanup errors
    }
  }
}

// ---------------------------------------------------------------------------
// Spawn pipeline.py
// ---------------------------------------------------------------------------

function runPipeline({ restaurant_name, address, competitors, outputPath }) {
  return new Promise((resolve, reject) => {
    // Locate pipeline.py relative to this file so it works regardless of cwd
    const scriptPath = path.join(__dirname, "pipeline.py");

    const args = [
      scriptPath,
      "--name",
      restaurant_name,
      "--address",
      address,
      "--competitors",
      competitors,
      "--output",
      outputPath,
    ];

    const python = process.env.PYTHON_BIN || "python3";
    console.log(`[pipeline] Running: ${python} pipeline.py --name "${restaurant_name}" ...`);

    const child = spawn(python, args, {
      env: { ...process.env },
      // Keep stdout/stderr as streams (don't buffer indefinitely)
    });

    child.stdout.on("data", (data) =>
      data
        .toString()
        .split("\n")
        .filter(Boolean)
        .forEach((line) => console.log(`[pipeline] ${line}`))
    );

    child.stderr.on("data", (data) =>
      data
        .toString()
        .split("\n")
        .filter(Boolean)
        .forEach((line) => console.log(`[pipeline] ${line}`))
    );

    child.on("error", (err) => reject(new Error(`Failed to spawn Python: ${err.message}`)));

    child.on("close", (code) => {
      if (code === 0) {
        resolve();
      } else {
        reject(new Error(`pipeline.py exited with code ${code}`));
      }
    });
  });
}

// ---------------------------------------------------------------------------
// POST callback to Supabase
// ---------------------------------------------------------------------------

function sendCallback({ prospect_id, slug, fileBase64, filename }) {
  return new Promise((resolve, reject) => {
    const body = JSON.stringify({ prospect_id, slug, file_base64: fileBase64, filename });

    const parsed = new URL(CALLBACK_URL);
    const isHttps = parsed.protocol === "https:";
    const lib = isHttps ? https : http;

    const options = {
      hostname: parsed.hostname,
      port: parsed.port || (isHttps ? 443 : 80),
      path: parsed.pathname,
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Content-Length": Buffer.byteLength(body),
      },
    };

    const req = lib.request(options, (res) => {
      let data = "";
      res.on("data", (chunk) => (data += chunk));
      res.on("end", () => {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          console.log(`[callback] Success (HTTP ${res.statusCode})`);
          resolve();
        } else {
          reject(
            new Error(
              `Callback returned HTTP ${res.statusCode}: ${data.slice(0, 200)}`
            )
          );
        }
      });
    });

    req.on("error", reject);
    req.write(body);
    req.end();
  });
}

// ---------------------------------------------------------------------------
// Start
// ---------------------------------------------------------------------------

app.listen(PORT, () => {
  console.log(`Restaurant Menu Scraper API listening on port ${PORT}`);
});
