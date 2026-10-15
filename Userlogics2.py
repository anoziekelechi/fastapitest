













// src/lib/handleApiError.ts

const DEFAULT_ERROR_CODE_REDIRECTS: Partial<Record<ApiErrorCode, string>> = {
  account_suspended:   "/contact-admin",
  account_unverified:  "/register/verify",
  firm_suspended:      "/contact-admin",
  no_permission:       "/",
  wrong_permission:    "/",
  no_country_scope:    "/",   // ← kept for backwards compat; harmless if unused
  wrong_country_scope: "/",   // ← same
  not_admin:           "/",
};





























// src/components/Footer.tsx













// src/types/payment/read.ts

export interface PaymentMethodRead {
  id: number;
  name: string;
  slug?: string | null;
  payment_logo_url?: string | null;
  created_at: string;
  updated_at: string;
}

export interface AvailablePaymentMethodsRead {
  total: number;
  methods: PaymentMethodRead[];
}


// src/types/payment/create.ts

import type { PaymentMethodRead } from "./read";

export interface PaymentMethodCreate {
  name: string;
}

export interface CreatePaymentMethodResponse {
  message: string;
  payment_method: PaymentMethodRead;
}

// src/types/payment/update.ts

import type { PaymentMethodRead } from "./read";

export interface PaymentMethodUpdate {
  name?: string;
}

export interface UpdatePaymentMethodResponse {
  message: string;
  payment_method: PaymentMethodRead;
}






// src/hooks/useAvailablePaymentMethods.ts

import { useEffect, useState } from "react";
import axios from "axios";

import api from "@/api/client";
import type { AvailablePaymentMethodsRead } from "@/types";


interface UseAvailablePaymentMethodsResult {
  data: AvailablePaymentMethodsRead | null;
  loading: boolean;
  error: string | null;
}


/**
 * Fetch every payment method accepted on the platform.
 *
 * Public endpoint — used by the Footer / landing page widget.
 */
export function useAvailablePaymentMethods(): UseAvailablePaymentMethodsResult {
  const [data, setData] = useState<AvailablePaymentMethodsRead | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    let mounted = true;

    api
      .get<AvailablePaymentMethodsRead>("/payment-methods/available", {
        signal: controller.signal,
      })
      .then((res) => {
        if (mounted) setData(res.data);
      })
      .catch((err) => {
        if (axios.isCancel(err)) return;
        if (!mounted) return;

        if (!axios.isAxiosError(err)) {
          setError("An unexpected error occurred.");
          return;
        }

        const status = err.response?.status;
        const detail = err.response?.data?.detail;

        setError(
          typeof detail === "string"
            ? detail
            : `Request failed (${status ?? "unknown"}).`
        );
      })
      .finally(() => {
        if (mounted && !controller.signal.aborted) setLoading(false);
      });

    return () => {
      mounted = false;
      controller.abort();
    };
  }, []);

  return { data, loading, error };
}









