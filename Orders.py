
// src/pages/Register.tsx

import { useEffect, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useNavigate, Link, Navigate } from "react-router-dom";
import axios from "axios";

import Form from "react-bootstrap/Form";
import Button from "react-bootstrap/Button";
import Container from "react-bootstrap/Container";
import Alert from "react-bootstrap/Alert";
import Spinner from "react-bootstrap/Spinner";

import api from "@/api/client";
import { useAuth } from "@/context/AuthContext";
import type { CreateUser, RegisterResponse, CountryListRead } from "@/types";

const schema = z.object({
  surname: z.string().trim().min(2, "Surname must be at least 2 characters").max(100),
  othernames: z.string().trim().min(2, "Other names must be at least 2 characters").max(150),
  email: z.string().trim().email("Please enter a valid email address"),
  password: z.string().min(6, "Password must be at least 6 characters"),
  country_id: z.coerce.number().int().positive("Please select a country"),
});
type FormData = z.infer<typeof schema>;

const Register = () => {
  const navigate = useNavigate();
  const { user, isLoading: authLoading } = useAuth();

  const [countries, setCountries] = useState<{ id: number; name: string }[]>([]);
  const [countriesLoading, setCountriesLoading] = useState(true);
  const [serverError, setServerError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  const redirectRef = useRef<{
    email: string;
    reg_token: string;
    otp_attempts_used: number;
    otp_expires_in_seconds: number;
  } | null>(null);

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
    setError,
  } = useForm<FormData>({
    resolver: zodResolver(schema),
    defaultValues: { surname: "", othernames: "", email: "", password: "", country_id: 0 },
  });

  useEffect(() => {
    let mounted = true;
    (async () => {
      try {
        setCountriesLoading(true);
        const res = await api.get<CountryListRead>("/countries");
        if (mounted) setCountries(res.data.countries || []);
      } catch (err) {
        if (!mounted) return;
        const status = axios.isAxiosError(err) ? err.response?.status : undefined;
        const detail = axios.isAxiosError(err) ? err.response?.data?.detail : undefined;
        setServerError(
          typeof detail === "string"
            ? detail
            : `Request failed (${status ?? "unknown"}).`
        );
        setCountries([]);
      } finally {
        if (mounted) setCountriesLoading(false);
      }
    })();
    return () => { mounted = false; };
  }, []);

  useEffect(() => {
    const p = redirectRef.current;
    if (!successMessage || !p) return;
    const id = window.setTimeout(() => {
      navigate("/register/verify", { replace: true, state: p });
    }, 1200);
    return () => window.clearTimeout(id);
  }, [successMessage, navigate]);

  if (authLoading) {
    return (
      <Container className="py-5 text-center">
        <Spinner animation="border" />
        <p className="text-muted mt-2">Loading...</p>
      </Container>
    );
  }
  if (user) return <Navigate to="/profile" replace />;

  const onSubmit = async (data: FormData) => {
    setServerError(null);
    setSuccessMessage(null);
    redirectRef.current = null;

    const payload: CreateUser = {
      surname: data.surname.trim(),
      othernames: data.othernames.trim(),
      email: data.email.trim().toLowerCase(),
      password: data.password,
      country_id: data.country_id,
    };

    try {
      const res = await api.post<RegisterResponse>("/auth/register", payload);
      redirectRef.current = {
        email: res.data.email,
        reg_token: res.data.reg_token,
        otp_attempts_used: res.data.otp_attempts_used,
        otp_expires_in_seconds: res.data.otp_expires_in_seconds,
      };
      setSuccessMessage(res.data.message);
    } catch (err) {
      if (!axios.isAxiosError(err)) {
        setServerError("An unexpected error occurred.");
        return;
      }
      const status = err.response?.status;
      const detail = err.response?.data?.detail;

      if (status === 422 && Array.isArray(detail)) {
        detail.forEach((item: unknown) => {
          if (typeof item !== "object" || item === null) return;
          const v = item as { loc?: unknown[]; msg?: string };
          const field = v.loc?.[v.loc.length - 1];
          if (typeof field === "string" && v.msg) {
            setError(field as keyof FormData, { type: "server", message: v.msg });
          }
        });
        return;
      }

      setServerError(
        typeof detail === "string"
          ? detail
          : `Request failed (${status ?? "unknown"}).`
      );
    }
  };

  return (
    <Container className="py-5" style={{ maxWidth: 480 }}>
      <div className="bg-white p-4 rounded shadow-sm">
        <h1 className="h3 text-center mb-4">Create Account</h1>

        {successMessage && (
          <Alert variant="success" className="text-center">
            {successMessage}
            <div className="small mt-1">Redirecting to verification...</div>
          </Alert>
        )}
        {serverError && <Alert variant="danger" className="text-center">{serverError}</Alert>}

        <Form onSubmit={handleSubmit(onSubmit)} noValidate>
          <Form.Group className="mb-3" controlId="surname">
            <Form.Label>Surname</Form.Label>
            <Form.Control type="text" autoComplete="family-name"
              isInvalid={!!errors.surname} disabled={isSubmitting || successMessage !== null}
              {...register("surname")} />
            <Form.Control.Feedback type="invalid">{errors.surname?.message}</Form.Control.Feedback>
          </Form.Group>

          <Form.Group className="mb-3" controlId="othernames">
            <Form.Label>Other Names</Form.Label>
            <Form.Control type="text" autoComplete="given-name"
              isInvalid={!!errors.othernames} disabled={isSubmitting || successMessage !== null}
              {...register("othernames")} />
            <Form.Control.Feedback type="invalid">{errors.othernames?.message}</Form.Control.Feedback>
          </Form.Group>

          <Form.Group className="mb-3" controlId="email">
            <Form.Label>Email</Form.Label>
            <Form.Control type="email" autoComplete="email"
              isInvalid={!!errors.email} disabled={isSubmitting || successMessage !== null}
              {...register("email")} />
            <Form.Control.Feedback type="invalid">{errors.email?.message}</Form.Control.Feedback>
          </Form.Group>

          <Form.Group className="mb-3" controlId="password">
            <Form.Label>Password</Form.Label>
            <Form.Control type="password" autoComplete="new-password"
              isInvalid={!!errors.password} disabled={isSubmitting || successMessage !== null}
              {...register("password")} />
            <Form.Control.Feedback type="invalid">{errors.password?.message}</Form.Control.Feedback>
          </Form.Group>

          <Form.Group className="mb-4" controlId="country_id">
            <Form.Label>Country</Form.Label>
            <Form.Select isInvalid={!!errors.country_id}
              disabled={countriesLoading || isSubmitting || successMessage !== null}
              {...register("country_id")}>
              <option value={0}>{countriesLoading ? "Loading countries..." : "Select country"}</option>
              {countries.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </Form.Select>
            <Form.Control.Feedback type="invalid">{errors.country_id?.message}</Form.Control.Feedback>
          </Form.Group>

          <Button type="submit" variant="primary" className="w-100"
            disabled={isSubmitting || countriesLoading || successMessage !== null}>
            {isSubmitting ? "Creating..." : "Register"}
          </Button>
        </Form>

        <p className="text-center mt-4 mb-0 small">
          Already have an account? <Link to="/login">Login</Link>
        </p>
      </div>
    </Container>
  );
};

export default Register;




 # ------------------------------------------------------------------
    # 1. Validate country
    # ------------------------------------------------------------------
    if data.country_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Country is required",
        )

    country = await db.get(Country, data.country_id)
    if not country:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Country not found",
        )
