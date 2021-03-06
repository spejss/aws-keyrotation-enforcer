'''AWS KeyRotation Enforcer Module, can be used to enforce that AWS Access Keys are rotated regularly'''

import os
import sys
import boto3
import logging
import re

from datetime import datetime, timedelta, tzinfo


def __getAwsIamUserList(iamClient):
    '''read all AWS IAM Users, of the account'''
    response = iamClient.list_users()

    users = response['Users']

    while response['IsTruncated']:
        marker = response['Marker']
        response = iamClient.list_users(Marker=marker)

        users.append(response['Users'])

    return users


def __getAwsAccessKeyAge(iamClient, iamUsers):
    '''read the AWS IAM Access Key Age, for each provided user'''
    iamAccessKeys = {'Keys': []}

    for iamUser in iamUsers:
        response = iamClient.list_access_keys(UserName=iamUser['UserName'])

        accessKeyInfos = []

        for accessKey in response['AccessKeyMetadata']:
            accessKeyInfos.append({
                'AccessKeyId': accessKey['AccessKeyId'],
                'AccessKeyStatus': accessKey['Status'],
                'CreateDate': accessKey['CreateDate'],
                'ContactDetails': __getUserEmail(iamClient, iamUser['UserName'])
            })

        if len(accessKeyInfos) > 0:
            iamAccessKeys['Keys'].append({
                'UserName': iamUser['UserName'],
                'AccessKeyInfos': accessKeyInfos
            })

    return iamAccessKeys


def __getUserEmail(iamClient, userName):
    '''get user e-mail adresse from credential/user tags'''
    response = iamClient.get_user(UserName=userName)

    logger = logging.getLogger('aws-keyrotation')
    try:
        foundContact = False
        contactEmail = ''

        for tag in response['User']['Tags']:
            if tag['Key'] == 'Contact':
                foundContact = True
                contactEmail = tag['Value']
                break

        if not foundContact:
            raise KeyError

    except KeyError:
        logger.warning('Contact details for user not provided!')

    return contactEmail


def __getNotifyKeyAgeDate(ageDays):
    '''get datetime before which a notification should be send out'''
    notifyKeyAgeDate = datetime.now() - timedelta(days=ageDays)
    return notifyKeyAgeDate


def __getDeactivateKeyAgeDate(ageDays):
    '''get datetime before which a key is deactivated'''
    notifyKeyAgeDate = datetime.now() - timedelta(days=ageDays)
    return notifyKeyAgeDate


def __identifyKeyAges(iamClient, iamAccessKeys, notifyKeyAgeDate, deactivateKeyAgeDate):
    '''identify all old Access Keys'''
    sesClient = boto3.client('ses', region_name='eu-west-1')
    logger = logging.getLogger('aws-keyrotation')

    for iamAccessKey in iamAccessKeys['Keys']:
        for accessKeyInfo in iamAccessKey['AccessKeyInfos']:
            if accessKeyInfo['CreateDate'].replace(tzinfo=None) < notifyKeyAgeDate and not accessKeyInfo['CreateDate'].replace(tzinfo=None) < deactivateKeyAgeDate and accessKeyInfo['AccessKeyStatus'] == 'Active':
                logger.info('Old AWS Credentials found!')

                if not accessKeyInfo['ContactDetails'] == '':
                    logger.info('Notification will be send to: ' +
                                accessKeyInfo['ContactDetails'])

                    __notifyKeyAges(sesClient, accessKeyInfo)
                else:
                    logger.warning(
                        'Contact details for credentials not provided!'
                    )

            elif accessKeyInfo['CreateDate'].replace(tzinfo=None) < deactivateKeyAgeDate and accessKeyInfo['AccessKeyStatus'] == 'Active':
                iamClient.update_access_key(
                    UserName=iamAccessKey['UserName'],
                    AccessKeyId=accessKeyInfo['AccessKeyId'],
                    Status='Inactive'
                )
                logger.critical(
                    'AWS Access Key, with ID: ' +
                    accessKeyInfo['AccessKeyId'] + ' is now disabled.'
                )


def __notifyKeyAges(sesClient, keyInfo):
    '''Notify technical contact, that credential needs to be rotated'''
    logger = logging.getLogger('aws-keyrotation')
    mailPattern = r"(^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$)"

    try:
        sourceMail = os.environ['SOURCEMAIL']

        if re.match(mailPattern, sourceMail) is not None:
            logger.info('Valid E-Mail adresse provided')
        else:
            raise SyntaxError

    except KeyError:
        logger.warning('SOURCEMAIL environment variable not found')
        return
    except SyntaxError:
        logger.warning('SOURCEMAIL is not a valid e-mail.')
        logger.warning('Notifications will not be send.')
        return

    try:
        sesClient.send_email(
            Source=sourceMail,
            Destination={
                'ToAddresses': [
                    keyInfo['ContactDetails'],
                ]
            },
            Message={
                'Subject': {
                    'Data': 'Rotate your AWS Credentials (KeyID: ' + keyInfo['AccessKeyId'] + ')'
                },
                'Body': {
                    'Text': {
                        'Data': 'Dear ' + keyInfo['ContactDetails'] + ',\n\nPlease rotate your AWS Access Key immediately.\n It will be disabled otherwise shortly if not rotated.\n\n Your AWS Keyrotation Service'
                    }
                }
            }
        )
    except:
        logger.warning('Notification could not be send!')


def lambda_handler(event, context):
    logging.basicConfig(stream=sys.stdout)
    logger = logging.getLogger('aws-keyrotation')
    logger.setLevel(logging.INFO)

    try:
        notifyKeyAge = int(os.environ['NOTIFYKEYAGE'])
    except KeyError:
        logger.info(
            'NOTIFYKEYAGE environment variable not found.'
        )
        logger.info(
            'Fallback to default (Days=30)'
        )
        notifyKeyAge = 30

    deactivateKeyAge = notifyKeyAge + 7

    notifyKeyAgeDate = __getNotifyKeyAgeDate(notifyKeyAge)
    deactivateKeyAgeDate = __getDeactivateKeyAgeDate(deactivateKeyAge)

    iamClient = boto3.client('iam')
    iamUsers = __getAwsIamUserList(iamClient)
    iamAccessKeys = __getAwsAccessKeyAge(iamClient, iamUsers)

    __identifyKeyAges(iamClient, iamAccessKeys,
                      notifyKeyAgeDate, deactivateKeyAgeDate)

    return


if __name__ == "__main__":
    lambda_handler(None, None)
