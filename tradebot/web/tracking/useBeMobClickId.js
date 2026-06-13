/**
 * useBeMobClickId — React 19 Custom Hook
 * ========================================
 * Menangkap click_id dari URL parameter (?click_id=XYZ123),
 * menyimpan ke localStorage agar persisten, dan menyediakan
 * nilai ini untuk dikirim sebagai merchant_ref ke API Tripay.
 *
 * Usage:
 *   const { clickId } = useBeMobClickId();
 *   // kirim clickId sebagai merchant_ref di payload create-payment
 *
 * Flow integrasi:
 *   1. BeMob campaign → landing page?click_id={tracking_id}
 *   2. Hook ini menangkap click_id dari URL
 *   3. User klik "Beli Bot" → create-payment API dengan merchant_ref=click_id
 *   4. Tripay callback → webhook baca merchant_ref → S2S postback ke BeMob
 */

import { useState, useEffect, useCallback } from "react";

const STORAGE_KEY = "bemob_click_id";
const PARAM_NAME = "click_id";

interface UseBeMobClickIdReturn {
  clickId: string | null;
  /** Simpan manual (fallback jika URL param detection gagal) */
  setClickId: (id: string) => void;
  /** Hapus click_id dari storage */
  clearClickId: () => void;
}

export function useBeMobClickId(): UseBeMobClickIdReturn {
  const [clickId, setClickIdState] = useState<string | null>(() => {
    try {
      return localStorage.getItem(STORAGE_KEY) || null;
    } catch {
      return null;
    }
  });

  useEffect(() => {
    // 1. Cek URL parameter ?click_id=...
    try {
      const params = new URLSearchParams(window.location.search);
      const urlClickId = params.get(PARAM_NAME);

      if (urlClickId && urlClickId.trim()) {
        const trimmed = urlClickId.trim();
        localStorage.setItem(STORAGE_KEY, trimmed);
        setClickIdState(trimmed);
        return; // URL param always takes priority
      }
    } catch (err) {
      console.warn("[useBeMobClickId] Failed to read URL params:", err);
    }

    // 2. Fallback ke localStorage (sudah di-init di useState)
  }, []);

  const setClickId = useCallback((id: string) => {
    try {
      localStorage.setItem(STORAGE_KEY, id);
      setClickIdState(id);
    } catch (err) {
      console.error("[useBeMobClickId] Failed to save click_id:", err);
    }
  }, []);

  const clearClickId = useCallback(() => {
    try {
      localStorage.removeItem(STORAGE_KEY);
      setClickIdState(null);
    } catch (err) {
      console.error("[useBeMobClickId] Failed to clear click_id:", err);
    }
  }, []);

  return { clickId, setClickId, clearClickId };
}

// ── Payment Trigger Helper ──────────────────────────────────────────

interface CreatePaymentPayload {
  amount: number;
  method?: string;
  merchant_ref?: string;
}

interface CreatePaymentResponse {
  success: boolean;
  reference?: string;
  merchant_ref?: string;
  checkout_url?: string;
  pay_code?: string;
  qr_url?: string;
  error?: string;
}

/**
 * Kirim request create-payment ke backend dengan click_id sebagai merchant_ref.
 * Panggil ini saat user menekan tombol "Beli Bot".
 */
export async function createPaymentWithClickId(
  payload: CreatePaymentPayload,
): Promise<CreatePaymentResponse> {
  const API_BASE =
    import.meta.env.VITE_API_BASE || "https://phantomfx.aitradepulse.com";

  const clickId = (() => {
    try {
      return localStorage.getItem(STORAGE_KEY);
    } catch {
      return null;
    }
  })();

  const merchantRef =
    payload.merchant_ref || clickId || `LP-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

  try {
    const params = new URLSearchParams({
      amount: String(payload.amount),
      method: payload.method || "QRIS2",
    });

    const url = `${API_BASE}/api/create-payment?${params.toString()}`;

    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        merchant_ref: merchantRef,
      }),
    });

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`Payment API error ${response.status}: ${errorText}`);
    }

    return await response.json();
  } catch (err) {
    console.error("[createPaymentWithClickId] Failed:", err);
    return {
      success: false,
      error: err instanceof Error ? err.message : "Unknown payment error",
    };
  }
}
