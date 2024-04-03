from math import sqrt
from dataclasses import dataclass

"""
Maybes

This module makes it easier to handle arithmetic when making thousands 
of caculations where some of those values could be None. It's a poor 
facsimilie of mondaic designs that are standard in Haskell and Rust.

The library inclues three classes, though 'Maybe' should be thought of 
as abstract and not instantiated.

Some:
    This wraps an actual value and can be treated like a regular numeric 
    value when interacting with other Maybes.

Empty:
    This can be thought of vaguly like a None with all the NoneType errors 
    handled automatically. However the behavior is slightly more complex.

    -- In additive interactions, Empty acts like 0.
    -- In multiplicative interactions, operations with Empty return Empty
    -- It's recommended within the context of this project that division 
       by zero errors using maybes also return Empty

Both Some and Empty can only be raised to a non-maybe numeric power.

The 'make_maybe' function should be used when wrapping values that can be 
some numeric type or None.
"""


@dataclass(frozen=True, slots=True)
class Maybe:
    def __abs__(self):
        ...

    def __add__(self, _):
        ...

    def __sub__(self, _):
        ...

    def __neg__(self):
        ...

    def __pow__(self, _):
        ...

    def __mult__(self, _):
        ...

    def __truediv__(self, _):
        ...

    def __le__(self, _):
        ...

    def __lt__(self, _):
        ...


@dataclass(frozen=True, slots=True)
class Empty(Maybe):
    @property
    def inner(self):
        return None

    def __abs__(self):
        return Empty()

    def __add__(self, another: Maybe):
        match another:
            case Some(_):
                return another
            case Empty():
                return self
            case _:
                raise TypeError("Some types can only be added to another maybe")

    def __radd__(self, another: int | float | Maybe):
        match another:
            case int(value) | float(value):
                if value == 0:
                    return self
                raise TypeError("Some types can only be added to another maybe")
            case Maybe():
                return self + another

    def __sub__(self, another: Maybe | int | float):
        match another:
            case Some(_):
                return -another
            case Empty():
                return self
            case int() | float():
                return self
            case _:
                raise TypeError(
                    "Some types can only be subtracted with another maybe"
                )

    def __lt__(self, _: Maybe):
        return False

    def __le__(self, _: Maybe):
        return False

    def __mul__(self, _: int | float | Maybe):
        return self

    def __truediv__(self, another: Maybe):
        match another:
            case Some(_) | Empty():
                return self
            case int() | float():
                return self
            case _:
                raise TypeError(
                    f"Some types must be divided with an int, float, or maybe. Received {type(another)}"
                )

    def __rtruediv__(self, another: Maybe):
        match another:
            case Some(_) | Empty():
                return self
            case int() | float():
                return self
            case _:
                raise TypeError(
                    f"Some types must be divided with an int, float, or maybe. Received {type(another)}"
                )

    def __neg__(self):
        return self

    def __pow__(self, other: int | float) -> Maybe:
        if type(other) not in {int, float}:
            raise TypeError(
                "Must use a float or int as a power on a maybe, other maybes are not allowed!"
            )

        return self


@dataclass(frozen=True, slots=True)
class Some(Maybe):
    inner: float

    def __abs__(self):
        return Some(abs(self.inner))

    def __add__(self, another: Maybe):
        match another:
            case Some(value):
                return Some(self.inner + value)
            case Empty():
                return self
            case _:
                raise TypeError("Some types can only be added to another maybe")

    def __radd__(self, another: int | float | Maybe):
        match another:
            case int(value) | float(value):
                return Some(self.inner + value)

            case Maybe():
                return self + another

    def __le__(self, other: Maybe) -> bool:
        match other:
            case Some():
                return self.inner <= other.inner
            case Empty():
                return False
            case _:
                raise TypeError("Must compare with maybe!")

    def __lt__(self, other: Maybe) -> bool:
        match other:
            case Some():
                return self.inner < other.inner
            case Empty():
                return False
            case _:
                raise TypeError("Must compare with maybe!")

    def __neg__(self):
        return Some(-self.inner)

    def __sub__(self, another: Maybe):
        match another:
            case Some(_):
                return Some(self.inner - another.inner)
            case Empty():
                return self
            case int() | float():
                return Some(self.inner - another)
            case _:
                raise TypeError(
                    "Somes must be subtracted by a float, int or maybe. Received {type(another)}."
                )

    def __mul__(self, another: int | float | Maybe):
        match another:
            case Some(value):
                return Some(self.inner * value)
            case Empty():
                return Empty()
            case int() | float():
                return Some(self.inner * another)
            case _:
                raise TypeError(
                    "Some types can only be multiplied with an int, float or another maybe"
                )

    __rmul__ = __mul__

    def __truediv__(self, another: Maybe):
        match another:
            case Some(value):
                try:
                    return Some(self.inner / value)
                except ZeroDivisionError:
                    return Empty()
            case Empty():
                return Empty()
            case int() | float():
                return Some(self.inner / another)
            case _:
                raise TypeError(
                    f"Some types must be divided with an int, float, or maybe. Received {type(another)}"
                )

    def __rtruediv__(self, another):
        match another:
            case Some(_) | Empty():
                return self
            case int() | float():
                return Some(another / self.inner)
            case _:
                raise TypeError(
                    f"Some types must be divided with an int, float, or maybe. Received {type(another)}"
                )

    def __pow__(self, other: int | float) -> Maybe:
        if type(other) not in {int, float}:
            raise TypeError(
                "Must use a float or int as a power on a maybe, other maybes are not allowed!"
            )

        return Some(self.inner**other)


def make_maybe(value: Maybe | float | None, empty_zeros=False) -> Some | Empty:
    match value:
        case Some() | Empty():
            return value
        case None:
            return Empty()
        case float(value) | int(value):
            if (value == 0) & empty_zeros:
                return Empty()
            return Some(value)
        case _:
            raise TypeError(f"Can only wrap floats or Nones, received {value}")


def make_maybe_dict(
    dictionary: dict[str, float | None], empty_zeros=False
) -> dict[str, Maybe]:
    return {
        k: make_maybe(v, empty_zeros=empty_zeros) for k, v in dictionary.items()
    }


def serialize_maybes(maybe: Maybe) -> float | None:
    match maybe:
        case Some(inner=value):
            return round(value, 2)
        case Empty():
            return None


"""
The TearValue datatype provides a wrapper to handle estimate values and
margins of error together and provide systems to manage them reasonably
for APIs and the like.
"""


@dataclass(slots=True, frozen=True)
class TearValue:  # (Real)
    value: Maybe
    error: Maybe = Some(0)

    def __abs__(self) -> "TearValue":
        return TearValue(value=abs(self.value), error=self.error)

    def __add__(self, other: "TearValue") -> "TearValue":
        try:
            return TearValue(
                value=self.value + other.value,
                error=sqrt(self.error**2 + other.error**2),
            )
        except TypeError as e:
            print(self)
            print(other)

            raise e

    __radd__ = __add__

    def __ceil__(self) -> "TearValue":
        raise NotImplementedError("'ceil' doesn't work for CensusValues")

    def __floor__(self) -> "TearValue":
        raise NotImplementedError("'floor' doesn't work for CensusValues")

    def __floordiv__(self) -> "TearValue":
        raise NotImplementedError("'floordiv' doesn't work for CensusValues")

    def __le__(self, other: "TearValue") -> bool:
        return self.value <= other.value

    def __lt__(self, other: "TearValue") -> bool:
        return self.value < other.value

    def __mod__(self, _):
        raise NotImplementedError("'mod' doesn't work for CensusValues")

    def __mul__(self, other: int | float | Maybe) -> "TearValue":
        return TearValue(
            value=self.value * other,
            error=self.error * other,
        )

    def __sub__(self, other: "TearValue") -> "TearValue":
        return TearValue(
            value=self.value - other.value,
            error=sqrt(self.error**2 + other.error**2),
        )

    __rsub__ = __sub__

    def __truediv__(self, other) -> "TearValue":
        """
        'self' is the numerator, other, the denominator
        """

        match other:
            case TearValue():
                try:
                    new_val = self.value / other.value
                    try:
                        error = (
                            Some(
                                sqrt(
                                    (
                                        self.error**2
                                        - (new_val * other.error**2)
                                    ).inner
                                )
                            )
                            / other.value
                        )
                    except (TypeError, AttributeError) as e:
                        print(e)
                        error = Empty()

                    return TearValue(
                        value=new_val,
                        error=error,
                    )
                except (ValueError, ZeroDivisionError) as e:
                    match e:
                        case ValueError():
                            new_val = self.value / other.value
                            try:
                                error = (
                                    Some(
                                        sqrt(
                                            (
                                                self.error**2
                                                + (new_val * other.error**2)
                                            ).inner
                                        )
                                    )
                                    / other.value
                                )
                            except (TypeError, AttributeError) as e:
                                print(e)
                                error = Empty()
                            return TearValue(
                                value=new_val,
                                error=error,
                            )
                        case ZeroDivisionError():
                            raise ZeroDivisionError()
            case float() | int():
                return TearValue(
                    self.value / other,
                    self.error / other,
                )

    def __rtruediv__(self, other):
        raise TypeError(f"You cannot divide a {type(other)} by a TearValue")

    def __neg__(self) -> "TearValue":
        return TearValue(value=Some(-1) * self.value, error=self.error)

    def __str__(self) -> str:
        return f"{self.value.inner:.2f}±{self.error.inner:.2f}"


def build_tearvalue(estimate, error):
    pass
