"""Cursor pages (docs/CONTRACT.md "Pagination"): every list returns ``{data, next}``, and ``next`` goes back as
``after`` until it is None.

    page = bx.sessions.list(limit=50)   # one page: page.data, page.next
    for s in bx.sessions.list():         # every session, page after page
        ...
    async for s in abx.sessions.list():  # the same with AsyncBoxline
        ...
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Awaitable, Callable, Dict, Generator, Generic, Iterator, List, Optional, TypeVar

from ._base import camel

T = TypeVar("T")


class _PageBase(Generic[T]):
    def __init__(self, raw: Dict[str, Any], data: List[T]) -> None:
        #: The list's whole reply (``total``, ``limit``, ``nextAfter``… where the list has them).
        self.raw = raw
        #: The items on this page.
        self.data = data
        nxt = raw.get("next")
        #: The cursor of the following page (pass it as ``after``), or None on the last page.
        self.next: Optional[str] = nxt if isinstance(nxt, str) and nxt else None

    def has_next_page(self) -> bool:
        return self.next is not None

    def __getitem__(self, key: str) -> Any:
        # page["data"], page["total"], page["next"]: the dict the SDK returned before 1.0.
        return self.data if key == "data" else self.raw[key]

    def __getattr__(self, name: str) -> Any:
        raw = self.__dict__.get("raw") or {}
        for key in (name, camel(name)):
            if key in raw:
                return raw[key]
        raise AttributeError(name)

    def __len__(self) -> int:
        # The items on this page only (iterating goes on to the next pages).
        return len(self.data)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(items={len(self.data)}, next={self.next!r})"


class Page(_PageBase[T]):
    """One page of a list. Iterating over it yields every item from this page on, fetching the next pages."""

    def __init__(self, raw: Dict[str, Any], data: List[T], load: Callable[[Optional[str]], "Page[T]"]) -> None:
        super().__init__(raw, data)
        self._load = load

    def get_next_page(self) -> "Page[T]":
        """The following page (same filters). Raises on the last page: check has_next_page() first."""
        if self.next is None:
            raise RuntimeError("this is the last page (next is None)")
        return self._load(self.next)

    def iter_pages(self) -> Iterator["Page[T]"]:
        """This page and every page after it."""
        page: Page[T] = self
        yield page
        while page.has_next_page():
            page = page.get_next_page()
            yield page

    def __iter__(self) -> Iterator[T]:  # type: ignore[override]
        for page in self.iter_pages():
            yield from page.data


class Pager(Page[T]):
    """What list methods of :class:`boxline.Boxline` return: the first page, fetched right away."""

    def __init__(self, load: Callable[[Optional[str]], Page[T]]) -> None:
        first = load(None)
        super().__init__(first.raw, first.data, load)


class AsyncPage(_PageBase[T]):
    """One page of a list (async). ``async for`` over it yields every item from this page on."""

    def __init__(self, raw: Dict[str, Any], data: List[T], load: Callable[[Optional[str]], Awaitable["AsyncPage[T]"]]) -> None:
        super().__init__(raw, data)
        self._load = load

    async def get_next_page(self) -> "AsyncPage[T]":
        """The following page (same filters). Raises on the last page: check has_next_page() first."""
        if self.next is None:
            raise RuntimeError("this is the last page (next is None)")
        return await self._load(self.next)

    async def iter_pages(self) -> AsyncIterator["AsyncPage[T]"]:
        """This page and every page after it."""
        page: AsyncPage[T] = self
        yield page
        while page.has_next_page():
            page = await page.get_next_page()
            yield page

    async def __aiter__(self) -> AsyncIterator[T]:
        async for page in self.iter_pages():
            for item in page.data:
                yield item


class AsyncPager(Generic[T]):
    """What list methods of :class:`boxline.AsyncBoxline` return: ``await`` it for the first page, or ``async for``
    over it for every item. Nothing is requested until then."""

    def __init__(self, load: Callable[[Optional[str]], Awaitable[AsyncPage[T]]]) -> None:
        self._load = load

    def __await__(self) -> Generator[Any, None, AsyncPage[T]]:
        return self._load(None).__await__()

    async def __aiter__(self) -> AsyncIterator[T]:
        page = await self._load(None)
        async for item in page:
            yield item
