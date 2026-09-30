use axum::extract::{DefaultBodyLimit, State};
use axum::http::StatusCode;
use axum::response::sse::{Event, Sse};
use axum::response::{IntoResponse, Response};
use axum::{Json, Router, routing::post};
use serde_json::{Value, json};
use std::convert::Infallible;
use std::io::Write;
use std::time::Duration;
use tokio::io::AsyncWriteExt;
use tokio::sync::mpsc;

type Telemetry = mpsc::UnboundedSender<Value>;

async fn completion(State(telemetry): State<Telemetry>, Json(input): Json<Value>) -> Response {
    let marker = input
        .get("messages")
        .and_then(Value::as_array)
        .and_then(|messages| messages.last())
        .and_then(|message| message.get("content"))
        .and_then(Value::as_str)
        .filter(|marker| marker.starts_with("benchmark-") && marker.len() <= 128);
    let Some(marker) = marker.map(str::to_owned) else {
        let _ = telemetry.send(json!({"event":"error", "kind":"invalid_benchmark_request"}));
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"error":{"code":"invalid_benchmark_request"}})),
        )
            .into_response();
    };
    let started = tokio::time::Instant::now();
    let chunk = |delta: Value, finish: Value| {
        json!({
            "id": marker, "object":"chat.completion.chunk", "created":0,
            "model":"gpt-5-mini-chat", "choices":[{"index":0,"delta":delta,"finish_reason":finish}]
        })
    };
    let role = chunk(json!({"role":"assistant"}), Value::Null).to_string();
    let content = chunk(json!({"content":" x".repeat(10)}), Value::Null).to_string();
    let mut terminal = chunk(json!({}), json!("stop"));
    terminal["usage"] = json!({"prompt_tokens":10,"completion_tokens":200,"total_tokens":210});
    let terminal = terminal.to_string();
    let stream = futures_util::stream::unfold(
        (0u32, role, content, terminal, marker, telemetry),
        move |(index, role, content, terminal, marker, telemetry)| async move {
            let data = match index {
                0 => {
                    tokio::time::sleep_until(started + Duration::from_millis(300)).await;
                    role.clone()
                }
                1..=20 => {
                    tokio::time::sleep_until(
                        started + Duration::from_millis(300 + u64::from(index) * 50),
                    )
                    .await;
                    content.clone()
                }
                21 => terminal.clone(),
                22 => {
                    // Publish service duration before downstream can receive its terminal event.
                    let _ = telemetry.send(json!({"event":"timing", "marker":marker,
                        "duration_ms":started.elapsed().as_secs_f64() * 1000.0}));
                    "[DONE]".to_owned()
                }
                _ => return None,
            };
            Some((
                Ok::<_, Infallible>(Event::default().data(data)),
                (index + 1, role, content, terminal, marker, telemetry),
            ))
        },
    );
    Sse::new(stream).into_response()
}

#[tokio::main(flavor = "multi_thread", worker_threads = 4)]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await?;
    let ready = json!({"event":"ready", "port":listener.local_addr()?.port(), "workers":4});
    {
        let mut stdout = std::io::stdout().lock();
        writeln!(stdout, "{ready}")?;
        stdout.flush()?;
    }
    let (telemetry, mut messages) = mpsc::unbounded_channel::<Value>();
    tokio::spawn(async move {
        let mut stdout = tokio::io::stdout();
        while let Some(value) = messages.recv().await {
            let mut bytes = serde_json::to_vec(&value).expect("telemetry JSON");
            bytes.push(b'\n');
            if stdout.write_all(&bytes).await.is_err() || stdout.flush().await.is_err() {
                std::process::exit(2);
            }
        }
    });
    let app = Router::new()
        .route("/v1/chat/completions", post(completion))
        .layer(DefaultBodyLimit::max(1024 * 1024))
        .with_state(telemetry);
    axum::serve(listener, app).await?;
    Ok(())
}
