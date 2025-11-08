import PirateBayAPI
import typing

# Static Typing isn't required (but recomended)
results: typing.List[PirateBayAPI.SearchElement] = PirateBayAPI.Search(
    "epub", PirateBayAPI.VideoType.Videos)

for result in results:
    print("File: {} {}.Mb (id:{})".format(
        result.name, round(result.size/1024/1024, 2), result.id))
