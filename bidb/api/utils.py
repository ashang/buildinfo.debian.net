import re
import hashlib

from debian import deb822
from dateutil.parser import parse

from django.db import transaction, IntegrityError
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from bidb.keys.models import Key
from bidb.packages.models import Source, Architecture, Binary
from bidb.buildinfo.models import Buildinfo, Origin

SUPPORTED_FORMATS = {'0.2', '1.0'}

re_binary = re.compile(
    r'^(?P<name>[^_]+)_(?P<version>[^_]+)_(?P<architecture>[^\.]+)\.u?deb$',
)
re_installed_build_depends = re.compile(
    r'^(?P<package>[^ ]+) \(= (?P<version>.+)\)'
)


class InvalidSubmission(Exception):
    pass

@transaction.atomic
def parse_submission(request):
    raw_text = request.read()

    try:
        data = deb822.Deb822(raw_text)
    except TypeError:
        raise InvalidSubmission("Could not parse RFC-822 format data.")

    raw_text_gpg_stripped = data.dump()

    ## Parse GPG info #########################################################

    uid = None
    data.raw_text = raw_text
    gpg_info = data.get_gpg_info()

    for x in ('VALIDSIG', 'NO_PUBKEY'):
        try:
            uid = gpg_info[x][0]
            break
        except (KeyError, IndexError):
            pass

    if uid is None:
        raise InvalidSubmission("Could not determine GPG uid")

    ## Check whether .buildinfo already exists ################################

    def create_submission(buildinfo):
        submission = buildinfo.submissions.create(
            key=Key.objects.get_or_create(uid=uid)[0],
        )

        default_storage.save(
            submission.get_storage_name(),
            ContentFile(raw_text),
        )

        return submission

    ## Parse new .buildinfo ###################################################

    def get_or_create(model, field):
        try:
            return model.objects.get_or_create(name=data[field])[0]
        except KeyError:
            raise InvalidSubmission("Missing required field: {}".format(field))

    if data.get('Format') not in SUPPORTED_FORMATS:
        raise InvalidSubmission(
            "Only {} 'Format:'  versions are supported".format(
                ', '.join(sorted(SUPPORTED_FORMATS)),
            )
        )

    sha1 = hashlib.sha1(raw_text_gpg_stripped.encode('utf-8')).hexdigest()

    try:
        with transaction.atomic():
            buildinfo = Buildinfo.objects.create(
                sha1=sha1,

                source=get_or_create(Source, 'Source'),
                architecture=get_or_create(Architecture, 'Architecture'),
                version=data['version'],

                build_path=data.get('Build-Path', ''),
                build_date=parse(data.get('Build-Date', '')),
                build_origin=get_or_create(Origin, 'Build-Origin'),
                build_architecture=get_or_create(Architecture, 'Build-Architecture'),

                environment=data.get('Environment', ''),
            )
    except IntegrityError:
        # Already exists; just attach a new Submission instance
        return create_submission(Buildinfo.objects.get(sha1=sha1)), False

    default_storage.save(
        buildinfo.get_storage_name(),
        ContentFile(raw_text_gpg_stripped),
    )

    ## Parse binaries #########################################################

    try:
        binary_names = set(data['Binary'].split(' '))
    except KeyError:
        raise InvalidSubmission("Missing 'Binary' field")

    if not binary_names:
        raise InvalidSubmission("Invalid 'Binary' field")

    binaries = {}
    for x in binary_names:
        # Save instances for lookup later
        binaries[x] = buildinfo.binaries.create(
            binary=Binary.objects.get_or_create(name=x)[0],
        )

    ## Parse checksums ########################################################

    hashes = ('Md5', 'Sha1', 'Sha256')

    checksums = {}
    for x in hashes:
        for y in data['Checksums-%s' % x].strip().splitlines():
            checksum, size, filename = y.strip().split()

            # Check size
            try:
                size = int(size)
                if size < 0:
                    raise ValueError()
            except ValueError:
                raise InvalidSubmission(
                    "Invalid size for {}: {}".format(filename, size),
                )

            checksums.setdefault(filename, {
                'size': size,
                'binary': None,
            })['checksum_{}'.format(x.lower())] = checksum

            existing = checksums[filename]['size']
            if size != existing:
                raise InvalidSubmission("Mismatched file size in "
                    "Checksums-{}: {} != {}".format(x, existing, size))

    ## Create Checksum instances ##############################################

    for k, v in sorted(checksums.items()):
        # Match with Binary instances if possible
        m = re_binary.match(k)
        if m is not None:
            v['binary'] = binaries.get(m.group('name'))

        buildinfo.checksums.create(filename=k, **v)

    ## Validate Installed-Build-Depends #######################################

    for x in data['Installed-Build-Depends'].strip().splitlines():
        m = re_installed_build_depends.match(x.strip())

        if m is None:
            raise InvalidSubmission(
                "Invalid entry in Installed-Build-Depends: {}".format(x),
            )

    return create_submission(buildinfo), True
