





# api/payments/logics.py (addition)



# api/payments/routes.py (addition)







# 1. Literal path — most specific
@router.get("/available", ...)                          # ← NEW, first

# 2. Literal prefix + param
@router.get("/country/{country_slug}", ...)             # public per-country

# 3. Dynamic path — least specific
@router.get("/{slug}", ...)                             # single method (admin)




// src/types/payment/read.ts (addition)

export interface AvailablePaymentMethodRead {
  id: number;
  name: string;
  slug?: string | null;
}

export interface AvailablePaymentMethodsRead {
  total: number;
  methods: AvailablePaymentMethodRead[];
}














// src/types/payment/read.ts

export interface PaymentMethodRead {
  id: number;
  country_id: number;
  name: string;
  slug?: string | null;
  payment_logo_url?: string | null;
  created_at: string;
  updated_at: string;
}

export interface CountryPaymentMethodsRead {
  country_id: number;
  country_name: string;
  payment_methods: PaymentMethodRead[];
}

export interface AllPaymentMethodsRead {
  total_countries: number;
  data: CountryPaymentMethodsRead[];
}

export interface AvailablePaymentMethodRead {
  id: number;
  name: string;
  slug?: string | null;
  payment_logo_url?: string | null;
}

export interface AvailablePaymentMethodsRead {
  total: number;
  methods: AvailablePaymentMethodRead[];
}







