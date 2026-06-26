"""Multi-page, feature-rich Reflex app for the reflex-desktop example.

Every widget is driven by the embedded (PyO3) backend over the websocket, so this doubles
as an end-to-end check that the desktop shell handles real apps: typed state vars, computed
vars, sync + async/background events, streamed updates, list mutation, forms, conditional
rendering, iteration — and multi-page routing with state shared across pages. Backend state
is persisted by Reflex's disk state manager (see the app's ``.states/`` directory).
"""

import asyncio

import reflex as rx

from reflex_desktop import desktop


class State(rx.State):
    """Backend-held state, shared across every page."""

    # --- base vars (several types) ---
    count: int = 0
    step: int = 1
    name: str = ""
    show_details: bool = True
    items: list[str] = ["learn reflex", "ship a desktop app"]
    new_item: str = ""
    running: bool = False

    # --- computed vars (recomputed server-side on dependency change) ---
    @rx.var
    def doubled(self) -> int:
        """Twice the count."""
        return self.count * 2

    @rx.var
    def is_even(self) -> bool:
        """Whether the count is even."""
        return self.count % 2 == 0

    @rx.var
    def greeting(self) -> str:
        """Greeting derived from the name var."""
        return f"Hello, {self.name.strip() or 'stranger'}!"

    @rx.var
    def item_count(self) -> int:
        """Number of items in the list."""
        return len(self.items)

    @rx.var
    def progress_value(self) -> int:
        """Count clamped to 0..10 and scaled to a 0..100 percentage."""
        return max(0, min(self.count, 10)) * 10

    # --- sync events ---
    @rx.event
    def increment(self):
        """Increase the count by the current step."""
        self.count += self.step

    @rx.event
    def decrement(self):
        """Decrease the count by the current step."""
        self.count -= self.step

    @rx.event
    def reset_count(self):
        """Reset the count and toast."""
        self.count = 0
        return rx.toast.info("Counter reset")

    @rx.event
    def set_step(self, value: str):
        """Set the increment step (string -> int)."""
        self.step = int(value) if value.strip() else 1

    @rx.event
    def set_name(self, value: str):
        """Bind the name input."""
        self.name = value

    @rx.event
    def toggle_details(self, value: bool):
        """Toggle the details callout."""
        self.show_details = value

    @rx.event
    def set_new_item(self, value: str):
        """Bind the new-item input."""
        self.new_item = value

    @rx.event
    def add_item(self):
        """Append the typed item to the list var and clear the input."""
        item = self.new_item.strip()
        if item:
            self.items.append(item)
            self.new_item = ""

    @rx.event
    def remove_item(self, item: str):
        """Remove an item (argument bound from the foreach iteration var)."""
        if item in self.items:
            self.items.remove(item)

    # --- async background event (streams deltas over the socket) ---
    @rx.event(background=True)
    async def count_to_ten(self):
        """Increment to ten on a background task, streaming an update each tick.

        Yields:
            A success toast once finished.
        """
        async with self:
            if self.running:
                return
            self.running = True
            self.count = 0
        for _ in range(10):
            await asyncio.sleep(0.25)
            async with self:
                self.count += 1
        async with self:
            self.running = False
        yield rx.toast.success("Counted to ten on a background task!")


def navbar() -> rx.Component:
    """Top navigation shared by every page, with a live shared-state count badge.

    Returns:
        The navbar component.
    """
    return rx.hstack(
        rx.link("Counter", href="/"),
        rx.link("Lists", href="/lists"),
        rx.link("About", href="/about"),
        rx.spacer(),
        rx.badge("shared count: ", State.count, color_scheme="iris"),
        width="100%",
        align="center",
        spacing="5",
    )


def shell(title: str, *children) -> rx.Component:
    """Page chrome: navbar + heading + content, centered.

    Args:
        title: Page heading.
        children: Page content.

    Returns:
        The page component.
    """
    return rx.center(
        rx.vstack(
            navbar(),
            rx.divider(),
            rx.heading(title, size="7"),
            *children,
            spacing="4",
            width="44em",
            max_width="92vw",
        ),
        padding="2em",
    )


def feature_card(title: str, *children) -> rx.Component:
    """Wrap a feature demo in a titled card.

    Args:
        title: Card heading.
        children: Demo components.

    Returns:
        A card component.
    """
    return rx.card(
        rx.vstack(rx.heading(title, size="4"), *children, spacing="3", align="start"),
        width="100%",
    )


def index() -> rx.Component:
    """Counter page (route ``/``): int var, computed vars, events, background task.

    Returns:
        The page component.
    """
    return shell(
        "Counter",
        feature_card(
            "int var · computed vars · events · background task",
            rx.hstack(
                rx.button("−", on_click=State.decrement, color_scheme="red", size="3"),
                rx.heading(State.count, size="8", width="3rem", text_align="center"),
                rx.button("+", on_click=State.increment, color_scheme="green", size="3"),
                rx.button("reset", on_click=State.reset_count, variant="soft"),
                align="center",
                spacing="4",
            ),
            rx.hstack(
                rx.text("step:"),
                rx.input(
                    type="number",
                    value=State.step.to_string(),
                    on_change=State.set_step,
                    width="5em",
                ),
                rx.badge("doubled = ", State.doubled),
                rx.cond(
                    State.is_even,
                    rx.badge("even", color_scheme="blue"),
                    rx.badge("odd", color_scheme="orange"),
                ),
                align="center",
            ),
            rx.progress(value=State.progress_value),
            rx.button(
                rx.cond(State.running, "counting…", "count to 10 (background)"),
                on_click=State.count_to_ten,
                disabled=State.running,
                variant="outline",
            ),
        ),
        feature_card(
            "native window controls (Reflex → Tauri bridge)",
            rx.text("Driven from Python events via rx.call_script → window.__TAURI__.", size="2"),
            rx.hstack(
                rx.button("minimize", on_click=desktop.minimize(), variant="soft"),
                rx.button("max / restore", on_click=desktop.toggle_maximize(), variant="soft"),
                rx.button(
                    "notify",
                    on_click=desktop.notify("Reflex 🤝 Tauri", "Native notification from Python!"),
                    variant="soft",
                ),
                spacing="2",
                wrap="wrap",
            ),
        ),
        rx.text("Navigate to other pages — the count above is shared backend state.", size="2"),
    )


def lists_page() -> rx.Component:
    """Lists page (route ``/lists``): string var, list var, form, foreach, cond.

    Returns:
        The page component.
    """
    return shell(
        "Lists & forms",
        feature_card(
            "string var · string computed var",
            rx.input(placeholder="your name", value=State.name, on_change=State.set_name),
            rx.text(State.greeting, weight="bold"),
        ),
        feature_card(
            "bool var · conditional rendering",
            rx.hstack(
                rx.switch(checked=State.show_details, on_change=State.toggle_details),
                rx.text("show details"),
                align="center",
            ),
            rx.cond(
                State.show_details,
                rx.callout("cond + bool var works — toggle the switch.", icon="info"),
            ),
        ),
        feature_card(
            "list var · form · foreach",
            rx.hstack(
                rx.input(
                    placeholder="add an item",
                    value=State.new_item,
                    on_change=State.set_new_item,
                    width="100%",
                ),
                rx.button("add", on_click=State.add_item),
                width="100%",
            ),
            rx.text("items: ", State.item_count),
            rx.foreach(
                State.items,
                lambda item: rx.hstack(
                    rx.text("•"),
                    rx.text(item),
                    rx.button(
                        "✕",
                        on_click=State.remove_item(item),
                        size="1",
                        variant="ghost",
                        color_scheme="red",
                    ),
                    align="center",
                    spacing="2",
                ),
            ),
        ),
    )


def about_page() -> rx.Component:
    """About page (route ``/about``): proves state is shared across navigation.

    Returns:
        The page component.
    """
    return shell(
        "About",
        rx.text("Reflex 🤝 Tauri — the Python ASGI backend runs in-process via PyO3."),
        rx.text("Every event on the other pages travels over the websocket to that backend."),
        feature_card(
            "shared state across pages",
            rx.text("count = ", State.count, " · doubled = ", State.doubled),
            rx.text("items = ", State.item_count, " · greeting = ", State.greeting),
            rx.text(
                "These match whatever you set on the Counter/Lists pages — same backend state.",
                size="2",
                color_scheme="gray",
            ),
        ),
        rx.link(rx.button("← back to counter"), href="/"),
    )


app = rx.App()
app.add_page(index, route="/", title="Counter · Reflex Tauri")
app.add_page(lists_page, route="/lists", title="Lists · Reflex Tauri")
app.add_page(about_page, route="/about", title="About · Reflex Tauri")
