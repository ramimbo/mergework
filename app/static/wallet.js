const encoder = new TextEncoder();

function bytesToHex(bytes) {
  return Array.from(new Uint8Array(bytes), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function hexToBytes(hex) {
  const clean = hex.trim().toLowerCase();
  if (clean.length % 2 !== 0 || !/^[0-9a-f]*$/.test(clean)) {
    throw new Error("invalid hex");
  }
  const bytes = new Uint8Array(clean.length / 2);
  for (let index = 0; index < clean.length; index += 2) {
    bytes[index / 2] = parseInt(clean.slice(index, index + 2), 16);
  }
  return bytes;
}

function stableJson(value) {
  if (Array.isArray(value)) {
    return `[${value.map(stableJson).join(",")}]`;
  }
  if (value && typeof value === "object") {
    return `{${Object.keys(value)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${stableJson(value[key])}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

async function sha256Hex(bytes) {
  return bytesToHex(await crypto.subtle.digest("SHA-256", bytes));
}

async function addressFromPublicKey(publicKeyHex) {
  const digest = await sha256Hex(hexToBytes(publicKeyHex));
  return `mrwk1${digest.slice(0, 40)}`;
}

function mrwkToMicrounits(amount) {
  const clean = amount.trim();
  const match = clean.match(/^([0-9]+)(?:\.([0-9]{1,6}))?$/);
  if (!match) {
    throw new Error("amount supports at most 6 decimal places");
  }
  const whole = Number.parseInt(match[1], 10);
  const fraction = (match[2] || "").padEnd(6, "0");
  const microunits = whole * 1_000_000 + Number.parseInt(fraction || "0", 10);
  if (!Number.isSafeInteger(microunits) || microunits <= 0) {
    throw new Error("invalid amount");
  }
  return microunits;
}

async function apiJson(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: {"Content-Type": "application/json", ...(options.headers || {})},
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `HTTP ${response.status}`);
  }
  return data;
}

async function importPrivateKey(privateKeyHex) {
  return crypto.subtle.importKey(
    "pkcs8",
    hexToBytes(privateKeyHex),
    {name: "Ed25519"},
    false,
    ["sign"],
  );
}

async function signPayload(privateKeyHex, payload) {
  const privateKey = await importPrivateKey(privateKeyHex);
  const signature = await crypto.subtle.sign("Ed25519", privateKey, encoder.encode(stableJson(payload)));
  return bytesToHex(signature);
}

function setText(selector, value) {
  const element = document.querySelector(selector);
  if (element) {
    element.textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  }
}

function setField(id, value) {
  const element = document.getElementById(id);
  if (element) {
    element.value = value;
  }
}

async function getWallet(address) {
  const clean = address.trim().toLowerCase();
  if (!clean) {
    throw new Error("wallet address is required");
  }
  return apiJson(`/api/v1/wallets/${encodeURIComponent(clean)}`);
}

async function getNextNonce(address, statusSelector) {
  const wallet = await getWallet(address);
  setText(statusSelector, `Transaction number ${wallet.next_nonce} will be used automatically.`);
  return wallet.next_nonce;
}

function setupNoncePreview(form, addressName, statusSelector) {
  const addressInput = form.querySelector(`[name="${addressName}"]`);
  if (!addressInput) {
    return;
  }
  addressInput.addEventListener("change", async () => {
    const address = String(addressInput.value || "").trim();
    if (!address) {
      setText(statusSelector, "Transaction number is handled automatically.");
      return;
    }
    try {
      await getNextNonce(address, statusSelector);
    } catch (error) {
      setText(statusSelector, error.message);
    }
  });
}

async function generateWallet() {
  const keyPair = await crypto.subtle.generateKey({name: "Ed25519"}, true, ["sign", "verify"]);
  const publicKeyHex = bytesToHex(await crypto.subtle.exportKey("raw", keyPair.publicKey));
  const privateKeyHex = bytesToHex(await crypto.subtle.exportKey("pkcs8", keyPair.privateKey));
  const address = await addressFromPublicKey(publicKeyHex);
  setField("wallet-public-key", publicKeyHex);
  setField("wallet-private-key", privateKeyHex);
  setField("wallet-address", address);
  setText("[data-wallet-result]", {address, public_key_hex: publicKeyHex});
}

function setupWalletGenerator() {
  const button = document.querySelector('[data-action="generate-wallet"]');
  if (button) {
    button.addEventListener("click", async () => {
      try {
        await generateWallet();
      } catch (error) {
        setText("[data-wallet-result]", error.message);
      }
    });
  }

  const form = document.querySelector('form[data-action="register-wallet"]');
  if (form) {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const data = new FormData(form);
      try {
        const wallet = await apiJson("/api/v1/wallets/register", {
          method: "POST",
          body: JSON.stringify({
            public_key_hex: String(data.get("public_key_hex") || ""),
            label: String(data.get("label") || ""),
          }),
        });
        setText("[data-wallet-result]", wallet);
      } catch (error) {
        setText("[data-wallet-result]", error.message);
      }
    });
  }
}

function setupTransfer() {
  const form = document.querySelector('form[data-action="submit-transfer"]');
  if (!form) {
    return;
  }
  setupNoncePreview(form, "from_address", "[data-transfer-nonce-status]");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = new FormData(form);
    const fromAddress = String(data.get("from_address") || "").trim().toLowerCase();
    const toAddress = String(data.get("to_address") || "").trim().toLowerCase();
    const amount = String(data.get("amount_mrwk") || "").trim();
    const memo = String(data.get("memo") || "").trim();
    try {
      const nonce = await getNextNonce(fromAddress, "[data-transfer-nonce-status]");
      const payload = {
        type: "mrwk_transfer_v1",
        from_address: fromAddress,
        to_address: toAddress,
        amount_microunits: mrwkToMicrounits(amount),
        nonce,
        memo,
      };
      const signature = await signPayload(String(data.get("private_key_hex") || ""), payload);
      const transfer = await apiJson("/api/v1/transfers", {
        method: "POST",
        body: JSON.stringify({
          from_address: fromAddress,
          to_address: toAddress,
          amount_mrwk: amount,
          nonce,
          memo,
          signature_hex: signature,
        }),
      });
      setText("[data-transfer-result]", transfer);
      await getNextNonce(fromAddress, "[data-transfer-nonce-status]");
    } catch (error) {
      setText("[data-transfer-result]", error.message);
    } finally {
      clearPrivateKeyField(form);
    }
  });
}

function clearPrivateKeyField(form) {
  const privateKeyField = form.querySelector('[name="private_key_hex"]');
  if (privateKeyField) {
    privateKeyField.value = "";
  }
}

function setupGithubActions() {
  const root = document.querySelector("[data-github-tool]");
  if (!root) {
    return;
  }
  const githubLogin = root.getAttribute("data-github-login");
  for (const action of ["link-github", "claim-github"]) {
    const form = root.querySelector(`form[data-action="${action}"]`);
    if (!form) {
      continue;
    }
    const statusSelector =
      action === "link-github" ? "[data-link-nonce-status]" : "[data-claim-nonce-status]";
    setupNoncePreview(form, "address", statusSelector);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const data = new FormData(form);
      const address = String(data.get("address") || "").trim().toLowerCase();
      const type = action === "link-github" ? "mrwk_link_github_v1" : "mrwk_claim_github_v1";
      const resultSelector = action === "link-github" ? "[data-link-result]" : "[data-claim-result]";
      const url = action === "link-github" ? "/api/v1/wallets/link-github" : "/api/v1/github/claim";
      try {
        const nonce = await getNextNonce(address, statusSelector);
        const payload = {type, address, github_login: githubLogin, nonce};
        const signature = await signPayload(String(data.get("private_key_hex") || ""), payload);
        const result = await apiJson(url, {
          method: "POST",
          body: JSON.stringify({address, nonce, signature_hex: signature}),
        });
        setText(resultSelector, result);
        await getNextNonce(address, statusSelector);
      } catch (error) {
        setText(resultSelector, error.message);
      } finally {
        clearPrivateKeyField(form);
      }
    });
  }
}

setupWalletGenerator();
setupTransfer();
setupGithubActions();
