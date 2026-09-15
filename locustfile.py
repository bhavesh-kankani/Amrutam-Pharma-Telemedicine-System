import uuid
import random
from locust import HttpUser, task, between, tag


class TelemedicinePatientUser(HttpUser):
    """
    Simulates patient traffic navigating the Amrutam Telemedicine platform.
    Models realistic user think time (1 to 3 seconds), read heavy workflows
    (doctor search, slot availability browsing), and concurrent booking transactions
    (optimistic holds, booking confirmations with idempotency keys).
    """
    wait_time = between(1, 3)

    def on_start(self):
        """Prepares user session with unique test credentials."""
        self.user_email = f"loadtest_patient_{uuid.uuid4().hex[:8]}@example.com"
        self.password = "LoadTestSecurePass2026!"
        self.access_token = None
        self.auth_headers = {}

    @tag("read")
    @task(5)
    def test_browse_doctors(self):
        """Read: Doctor directory listing with filtering by specialization and fee."""
        specialization = random.choice(["Ayurveda", "Panchakarma", "Rasayana", "Nadi", ""])
        params = {}
        if specialization:
            params["specialization"] = specialization
        if random.choice([True, False]):
            params["min_fee"] = 100
            params["max_fee"] = 1000

        with self.client.get(
            "/api/v1/doctors/",
            params=params,
            name="/api/v1/doctors/ [search & filter]",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Doctor search failed with status {response.status_code}")

    @tag("read")
    @task(5)
    def test_list_availability_slots(self):
        """Read: Real-time availability slot listing with remaining hold countdown."""
        with self.client.get(
            "/api/v1/slots/",
            name="/api/v1/slots/ [availability list]",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Slot listing failed with status {response.status_code}")

    @tag("read")
    @task(2)
    def test_liveness_and_readiness_probes(self):
        """Read: Operational Kubernetes health probes."""
        self.client.get("/healthz/", name="/healthz/ [liveness]")
        self.client.get("/readyz/", name="/readyz/ [readiness]")

    @tag("read")
    @task(1)
    def test_prometheus_metrics(self):
        """Read: Prometheus telemetry scraping endpoint."""
        self.client.get("/metrics", name="/metrics [prometheus telemetry]")

    @tag("write")
    @task(2)
    def test_slot_hold_workflow(self):
        """Write: Atomic slot hold with 5-minute reservation."""
        slot_id = str(uuid.uuid4())
        payload = {"slot_id": slot_id}
        headers = {"X-Request-ID": str(uuid.uuid4())}

        with self.client.post(
            "/api/v1/bookings/hold/",
            json=payload,
            headers=headers,
            name="/api/v1/bookings/hold/ [slot hold]",
            catch_response=True,
        ) as response:
            if response.status_code in (200, 400, 401, 403, 409):
                response.success()
            else:
                response.failure(f"Unexpected status: {response.status_code}")

    @tag("write")
    @task(1)
    def test_booking_confirmation_workflow(self):
        """Write: Finalize booking transaction with idempotency token."""
        slot_id = str(uuid.uuid4())
        idempotency_key = f"locust_idem_{uuid.uuid4().hex}"
        payload = {
            "slot_id": slot_id,
            "amount": "500.00",
            "idempotency_key": idempotency_key,
            "payment_status": "success",
            "transaction_reference_number": f"TXN_{uuid.uuid4().hex[:10].upper()}",
        }
        headers = {
            "X-Request-ID": str(uuid.uuid4()),
            "X-Idempotency-Key": idempotency_key,
        }

        with self.client.post(
            "/api/v1/bookings/confirm/",
            json=payload,
            headers=headers,
            name="/api/v1/bookings/confirm/ [booking confirmation]",
            catch_response=True,
        ) as response:
            if response.status_code in (201, 400, 401, 403, 409):
                response.success()
            else:
                response.failure(f"Unexpected status: {response.status_code}")

    @tag("write")
    @task(1)
    def test_payment_webhook_ingestion(self):
        """Write: Asynchronous payment gateway webhook notification."""
        idem_key = f"webhook_idem_{uuid.uuid4().hex}"
        payload = {
            "event": "payment.success",
            "idempotency_key": idem_key,
            "data": {
                "transaction_id": f"TXN_{uuid.uuid4().hex[:8].upper()}",
                "amount": "500.00",
                "currency": "INR",
            },
        }
        headers = {
            "X-Request-ID": str(uuid.uuid4()),
            "X-Idempotency-Key": idem_key,
        }

        with self.client.post(
            "/api/v1/payments/webhook/",
            json=payload,
            headers=headers,
            name="/api/v1/payments/webhook/ [gateway callback]",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Webhook failed with status {response.status_code}")
